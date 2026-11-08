//! IGCZ: a compact, byte-exact encoding of IGC flight logs (experiments/igcz.py is the reference).
//!
//! The first part is bytes, every number written as a varint (see varint.zig):
//!
//!     "IGCZ" version
//!     line ending              0 = "\n", 1 = "\r\n"
//!     other lines              count, then their texts: lengths flag (+ lengths), size,
//!                              texts joined by "\n" and deflated with the primer as a preset
//!                              dictionary (their positions are in the coded part)
//!     fix count
//!     invalid fixes            count, then their indexes (as changes)
//!     extension fields         mode 0 (fields from the I-record): field count, then per
//!                                      field: width, kind (0 = number, 1 = text)
//!                              mode 1 (no field layout fits): nothing
//!                              then size + deflated text: text fields (mode 0), or all
//!                              tails (mode 1)
//!
//! The second part is one arithmetic-coded stream (see arith.zig), each column with its own
//! learned probabilities:
//!
//!     time, lat, lng, pressure_alt, gnss_alt     guess "same change as last time"
//!     mode 0: fields present on each fix,        guess "same value as last time"
//!             then each number field (only on the fixes that have it)
//!     mode 1: tail length of each fix
//!     position of each other line (index of the fix it comes before)
//!
//! "Other lines" are every line that is not a regular B-line (headers, events, signature,
//! malformed B-lines), kept as raw text with the index of the fix they come before.

const std = @import("std");
const Allocator = std.mem.Allocator;

const arith = @import("arith.zig");
const bline = @import("bline.zig");
const deflate = @import("deflate.zig");
const varint = @import("varint.zig");

pub const Fix = bline.Fix;

pub const magic = "IGCZ";
pub const version = 6;

/// Wider fields stay text: their values could overflow the coder.
const max_number_width = 12;
const field_number = 0;
const field_text = 1;

pub const EncodeError = error{
    OutOfMemory,
    /// The clock jumps back by more than 12 hours: the format refuses those flights.
    MidnightRollover,
    /// A value too large for the coder (cannot come from a B-line in practice).
    NumberTooLarge,
};

pub const DecodeError = error{
    OutOfMemory,
    /// Does not start with "IGCZ".
    NotIgcz,
    /// An IGCZ file of another format version.
    UnsupportedVersion,
    /// Truncated or damaged.
    Corrupt,
};

/// Encodes the bytes of an .igc file. The caller owns the result.
pub fn encode(gpa: Allocator, igc: []const u8) EncodeError![]u8 {
    var arena_state: std.heap.ArenaAllocator = .init(gpa);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    const crlf = std.mem.indexOf(u8, igc, "\r\n") != null;
    var fixes: std.ArrayList(Fix) = .empty;
    var others: std.ArrayList([]const u8) = .empty;
    var positions: std.ArrayList(i64) = .empty;
    var lines = std.mem.splitSequence(u8, igc, if (crlf) "\r\n" else "\n");
    while (lines.next()) |line| {
        const fix = bline.parse(line) orelse {
            try others.append(arena, line);
            try positions.append(arena, @intCast(fixes.items.len));
            continue;
        };
        if (fixes.items.len > 0 and isMidnightRollover(fixes.items[fixes.items.len - 1].time, fix.time))
            return error.MidnightRollover;
        try fixes.append(arena, fix);
    }

    var out: Out = .{ .gpa = gpa };
    errdefer out.list.deinit(gpa);
    try out.list.appendSlice(gpa, magic ++ [_]u8{version});
    try out.num(@intFromBool(crlf));

    try out.num(@intCast(others.items.len));
    try writeTexts(arena, &out, others.items);

    try out.num(@intCast(fixes.items.len));

    var invalid: std.ArrayList(i64) = .empty;
    for (fixes.items, 0..) |f, i| if (f.valid == 'V') try invalid.append(arena, @intCast(i));
    try out.num(@intCast(invalid.items.len));
    try out.changes(invalid.items);

    const tail_columns = try writeTail(arena, &out, fixes.items, others.items);

    var enc: arith.Encoder = .init(arena);
    inline for (.{ "time", "lat", "lng", "pressure_alt", "gnss_alt" }) |name| {
        const column = try arena.alloc(i64, fixes.items.len);
        for (column, fixes.items) |*v, f| v.* = @field(f, name);
        try arith.writeColumn(&enc, column, true);
    }
    for (tail_columns) |column| try arith.writeColumn(&enc, column, false);
    try arith.writeColumn(&enc, positions.items, false);
    try out.list.appendSlice(gpa, try enc.finish());

    return out.list.toOwnedSlice(gpa);
}

/// Rebuilds the exact bytes of the original .igc file. The caller owns the result.
pub fn decode(gpa: Allocator, data: []const u8) DecodeError![]u8 {
    if (data.len < magic.len + 1 or !std.mem.startsWith(u8, data, magic)) return error.NotIgcz;
    if (data[magic.len] != version) return error.UnsupportedVersion;

    var arena_state: std.heap.ArenaAllocator = .init(gpa);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    var r: varint.Reader = .{ .data = data, .pos = magic.len + 1 };
    const eol: []const u8 = if (try r.num() == 1) "\r\n" else "\n";

    const other_count = try r.len();
    const other_texts = try readTexts(arena, &r, other_count);

    const count = try r.len();
    const invalid = try arena.alloc(bool, count);
    @memset(invalid, false);
    var index: i64 = 0;
    for (0..try r.len()) |_| {
        index = std.math.add(i64, index, try r.num()) catch return error.Corrupt;
        const i = std.math.cast(usize, index) orelse return error.Corrupt;
        if (i >= count) return error.Corrupt;
        invalid[i] = true;
    }

    const fields, const raw_text = try readTail(arena, &r);

    var dec: arith.Decoder = .init(data, r.pos);
    var columns: [5][]i64 = undefined;
    for (&columns) |*column| column.* = try arith.readColumn(arena, &dec, count, true);
    const tails = try decodeTails(arena, &dec, count, fields, raw_text);
    const positions = try arith.readColumn(arena, &dec, other_count, false);

    var out: std.Io.Writer.Allocating = try .initCapacity(gpa, count * 45 + 1024);
    defer out.deinit();
    const w = &out.writer;
    var k: usize = 0;
    for (0..count + 1) |i| {
        while (k < other_count and positions[k] == i) : (k += 1) {
            if (k + i > 0) w.writeAll(eol) catch return error.OutOfMemory;
            w.writeAll(other_texts[k]) catch return error.OutOfMemory;
        }
        if (i < count) {
            if (k + i > 0) w.writeAll(eol) catch return error.OutOfMemory;
            bline.write(w, .{
                .time = columns[0][i],
                .lat = columns[1][i],
                .lng = columns[2][i],
                .valid = if (invalid[i]) 'V' else 'A',
                .pressure_alt = columns[3][i],
                .gnss_alt = columns[4][i],
                .tail = tails[i],
            }) catch return error.OutOfMemory;
        }
    }
    if (k != other_count) return error.Corrupt; // a position out of order or out of range
    return out.toOwnedSlice() catch error.OutOfMemory;
}

pub fn isMidnightRollover(previous_time: i64, time: i64) bool {
    // Loggers sometimes step back a few seconds; only a jump back of half a day is midnight.
    return time < previous_time - 12 * 3600;
}

/// The bytes part being written.
const Out = struct {
    list: std.ArrayList(u8) = .empty,
    gpa: Allocator,

    fn num(o: *Out, n: i64) error{OutOfMemory}!void {
        try varint.write(&o.list, o.gpa, n);
    }

    fn changes(o: *Out, values: []const i64) error{OutOfMemory}!void {
        var previous: i64 = 0;
        for (values) |v| {
            try o.num(v - previous);
            previous = v;
        }
    }

    fn blob(o: *Out, bytes: []const u8) error{OutOfMemory}!void {
        try o.num(@intCast(bytes.len));
        try o.list.appendSlice(o.gpa, bytes);
    }
};

/// Lines joined by "\n" and deflated with the primer, so common header phrases cost almost nothing.
fn writeTexts(arena: Allocator, out: *Out, lines: []const []const u8) error{OutOfMemory}!void {
    // With "\r\n" line endings a line may contain a lone "\n"; then joining is ambiguous, so store the lengths.
    var with_lengths = false;
    for (lines) |line| {
        if (std.mem.indexOfScalar(u8, line, '\n') != null) with_lengths = true;
    }
    try out.num(@intFromBool(with_lengths));
    if (with_lengths) for (lines) |line| try out.num(@intCast(line.len));
    try out.blob(try deflate.compress(arena, try std.mem.join(arena, "\n", lines)));
}

fn readTexts(arena: Allocator, r: *varint.Reader, count: usize) DecodeError![]const []const u8 {
    const with_lengths = try r.num() == 1;
    var lengths: []usize = &.{};
    if (with_lengths) {
        lengths = try arena.alloc(usize, count);
        for (lengths) |*n| n.* = try r.len();
    }
    const text = try deflate.decompress(arena, try r.bytes(try r.len()));
    const lines = try arena.alloc([]const u8, count);
    if (count == 0) return lines;
    if (with_lengths) {
        var offset: usize = 0;
        for (lines, lengths) |*line, n| {
            if (n > text.len -| offset) return error.Corrupt;
            line.* = text[offset..][0..n];
            offset += n + 1;
        }
    } else {
        var it = std.mem.splitScalar(u8, text, '\n');
        for (lines) |*line| line.* = it.next() orelse return error.Corrupt;
        if (it.next() != null) return error.Corrupt;
    }
    return lines;
}

/// Writes the extension layout and raw text. Returns the columns for the coded part.
///
/// mode 0: the tails are made of fields (I-record). Each fix stores how many fields it has,
///         number fields go to the coded part, other fields to the deflated text.
/// mode 1: no such layout. Each fix stores its tail length, the text goes deflated.
fn writeTail(arena: Allocator, out: *Out, fixes: []const Fix, others: []const []const u8) EncodeError![]const []const i64 {
    var columns: std.ArrayList([]const i64) = .empty;
    var raw: std.ArrayList(u8) = .empty;

    if (try tailLayout(arena, others, fixes)) |widths| {
        try out.num(0);
        try out.num(@intCast(widths.len));

        const present = try arena.alloc(i64, fixes.len);
        for (present, fixes) |*n, f| n.* = @intCast(fieldCount(widths, f.tail.len).?);
        try columns.append(arena, present);

        var offset: usize = 0;
        for (widths, 0..) |width, index| {
            var values: std.ArrayList([]const u8) = .empty;
            for (fixes, present) |f, n| {
                if (n > index) try values.append(arena, f.tail[offset..][0..width]);
            }
            try out.num(@intCast(width));
            if (try numberField(arena, values.items, width)) |numbers| {
                try out.num(field_number);
                try columns.append(arena, numbers);
            } else {
                try out.num(field_text);
                for (values.items) |v| try raw.appendSlice(arena, v);
            }
            offset += width;
        }
    } else {
        try out.num(1);
        const lengths = try arena.alloc(i64, fixes.len);
        for (lengths, fixes) |*n, f| {
            n.* = @intCast(f.tail.len);
            try raw.appendSlice(arena, f.tail);
        }
        try columns.append(arena, lengths);
    }
    // Usually empty: skip deflate's own bytes then.
    try out.blob(if (raw.items.len > 0) try deflate.compress(arena, raw.items) else "");
    return columns.items;
}

const Field = struct { width: usize, kind: i64 };

fn readTail(arena: Allocator, r: *varint.Reader) DecodeError!struct { ?[]const Field, []const u8 } {
    var fields: ?[]Field = null;
    if (try r.num() == 0) {
        const list = try arena.alloc(Field, try r.len());
        for (list) |*f| f.* = .{ .width = try r.len(), .kind = try r.num() };
        fields = list;
    }
    const size = try r.len();
    const raw = if (size > 0) try deflate.decompress(arena, try r.bytes(size)) else "";
    return .{ fields, raw };
}

fn decodeTails(arena: Allocator, dec: *arith.Decoder, count: usize, fields: ?[]const Field, raw: []const u8) DecodeError![]const []const u8 {
    const tails = try arena.alloc([]const u8, count);
    const layout = fields orelse {
        var offset: usize = 0;
        for (tails, try arith.readColumn(arena, dec, count, false)) |*tail, n| {
            const len = std.math.cast(usize, n) orelse return error.Corrupt;
            if (len > raw.len - offset) return error.Corrupt;
            tail.* = raw[offset..][0..len];
            offset += len;
        }
        return tails;
    };

    // Each tail is its first `present` fields: give every fix its slot in one buffer, then fill field by field.
    const present = try arith.readColumn(arena, dec, count, false);
    const ends = try arena.alloc(usize, layout.len + 1); // where each field ends inside a tail
    ends[0] = 0;
    for (layout, 0..) |f, i| ends[i + 1] = std.math.add(usize, ends[i], f.width) catch return error.Corrupt;
    var total: usize = 0;
    const starts = try arena.alloc(usize, count);
    for (present, starts) |n, *start| {
        if (n < 0 or n > layout.len) return error.Corrupt;
        start.* = total;
        total = std.math.add(usize, total, ends[@intCast(n)]) catch return error.Corrupt;
    }
    const buf = try arena.alloc(u8, total);

    var raw_offset: usize = 0;
    for (layout, 0..) |f, index| {
        var owners: std.ArrayList(usize) = .empty;
        for (present, 0..) |n, i| if (n > index) try owners.append(arena, i);
        if (f.kind == field_number) {
            const numbers = try arith.readColumn(arena, dec, owners.items.len, false);
            for (owners.items, numbers) |i, v| {
                var w: std.Io.Writer = .fixed(buf[starts[i] + ends[index] ..][0..f.width]);
                bline.writePadded(&w, v, f.width) catch return error.Corrupt;
                if (w.end != f.width) return error.Corrupt;
            }
        } else {
            for (owners.items) |i| {
                if (f.width > raw.len - raw_offset) return error.Corrupt;
                @memcpy(buf[starts[i] + ends[index] ..][0..f.width], raw[raw_offset..][0..f.width]);
                raw_offset += f.width;
            }
        }
    }
    for (tails, present, starts) |*tail, n, start| tail.* = buf[start..][0..ends[@intCast(n)]];
    return tails;
}

/// Field widths such that every tail is exactly its first few fields, or null if there is none.
fn tailLayout(arena: Allocator, others: []const []const u8, fixes: []const Fix) error{OutOfMemory}!?[]const usize {
    var longest: usize = 0;
    for (fixes) |f| longest = @max(longest, f.tail.len);

    var candidates: std.ArrayList([]const usize) = .empty;
    if (try iRecordWidths(arena, others)) |declared| {
        try candidates.append(arena, declared);
        var sum: usize = 0;
        for (declared) |w| sum += w;
        if (longest > sum) {
            // Some loggers write more than their I-record declares: one extra field for the rest.
            try candidates.append(arena, try std.mem.concat(arena, usize, &.{ declared, &.{longest - sum} }));
        }
    }
    try candidates.append(arena, if (longest > 0) try arena.dupe(usize, &.{longest}) else &.{});

    next: for (candidates.items) |widths| {
        for (fixes) |f| if (fieldCount(widths, f.tail.len) == null) continue :next;
        return widths;
    }
    return null;
}

/// How many whole fields make up `len` characters, or null if `len` ends inside a field.
fn fieldCount(widths: []const usize, len: usize) ?usize {
    var end: usize = 0;
    for (widths, 0..) |w, i| {
        if (end == len) return i;
        end += w;
    }
    return if (end == len) widths.len else null;
}

/// Widths of the extension fields declared by the I-record, or null.
fn iRecordWidths(arena: Allocator, others: []const []const u8) error{OutOfMemory}!?[]const usize {
    const line = for (others) |line| {
        if (line.len > 0 and line[0] == 'I') break line;
    } else return null;

    var widths: std.ArrayList(usize) = .empty;
    const field_count = pyInt(slice(line, 1, 3)) orelse return null;
    var expected_start: i64 = bline.tail_start + 1; // the I-record counts bytes from 1
    var k: usize = 0;
    while (k < field_count) : (k += 1) {
        const field = slice(line, 3 + 7 * k, 10 + 7 * k);
        const start = pyInt(slice(field, 0, 2)) orelse return null;
        const end = pyInt(slice(field, 2, 4)) orelse return null;
        if (start != expected_start or end < start) return null;
        try widths.append(arena, @intCast(end - start + 1));
        expected_start = end + 1;
    }
    return widths.items;
}

/// The values as numbers, if every one of them comes back identical once formatted.
fn numberField(arena: Allocator, values: []const []const u8, width: usize) error{OutOfMemory}!?[]const i64 {
    if (width > max_number_width) return null;
    const numbers = try arena.alloc(i64, values.len);
    for (numbers, values) |*n, v| {
        n.* = pyInt(v) orelse return null;
        var buf: [32]u8 = undefined;
        var w: std.Io.Writer = .fixed(&buf);
        bline.writePadded(&w, n.*, width) catch return null;
        if (!std.mem.eql(u8, w.buffered(), v)) return null;
    }
    return numbers;
}

/// `s[start..end]`, clamped to the string like a Python slice.
fn slice(s: []const u8, start: usize, end: usize) []const u8 {
    return s[@min(start, s.len)..@min(end, s.len)];
}

/// Python's int() on a latin-1 string (surrounding spaces, a sign, "_" between digits), so the
/// encoder picks the same layout as the reference implementation.
fn pyInt(text: []const u8) ?i64 {
    const s = std.mem.trim(u8, text, " \t\n\r\x0b\x0c\x1c\x1d\x1e\x1f\x85\xa0");
    var i: usize = 0;
    var negative = false;
    if (s.len > 0 and (s[0] == '+' or s[0] == '-')) {
        negative = s[0] == '-';
        i = 1;
    }
    var value: i64 = 0;
    var after_digit = false;
    for (s[i..]) |c| {
        if (std.ascii.isDigit(c)) {
            value = std.math.mul(i64, value, 10) catch return null;
            value = std.math.add(i64, value, c - '0') catch return null;
            after_digit = true;
        } else if (c == '_' and after_digit) {
            after_digit = false;
        } else return null;
    }
    if (!after_digit) return null;
    return if (negative) -value else value;
}

const testing = std.testing;

test {
    _ = arith;
    _ = bline;
    _ = deflate;
    _ = varint;
}

const header = "AXSR\nHFDTE130621\nI023638TAS3940SIU";

fn expectRoundTrip(igc: []const u8) !void {
    const gpa = testing.allocator;
    const encoded = try encode(gpa, igc);
    defer gpa.free(encoded);
    const decoded = try decode(gpa, encoded);
    defer gpa.free(decoded);
    try testing.expectEqualStrings(igc, decoded);
}

fn encodedLen(igc: []const u8) !usize {
    const encoded = try encode(testing.allocator, igc);
    defer testing.allocator.free(encoded);
    return encoded.len;
}

test "basic flight" {
    try expectRoundTrip(header ++
        "\nB1159204549164N00614910EA011700130802507" ++
        "\nB1159214549168N00614909EA011680130602607\n");
}

test "crlf and no final newline" {
    try expectRoundTrip("AXSR\r\nHFDTE130621\r\nI023638TAS3940SIU\r\nB1159204549164N00614910EA011700130802507");
}

test "south, west and invalid fix" {
    try expectRoundTrip(header ++
        "\nB1159203349164S07014910WV011700130802507" ++
        "\nB1159213349168S07014909WA011680130602607\n");
}

test "lines between fixes stay in place" {
    try expectRoundTrip(header ++
        "\nB1159204549164N00614910EA011700130802507" ++
        "\nE115920PEV\nLXSRsome comment" ++
        "\nB1159214549168N00614909EA011680130602607" ++
        "\nGABCDEF\n");
}

test "malformed B-lines are kept as text" {
    try expectRoundTrip(header ++
        "\nB1159204549164N00614910EA011700130802507" ++
        "\nB11592" ++
        "\nB1159214569999N00614909EA011680130602607" ++
        "\nB1159224549168N00614909EX011680130602607\n");
}

test "non-digit extensions" {
    try expectRoundTrip(header ++
        "\nB1159204549164N00614910EA0117001308ab507" ++
        "\nB1159214549168N00614909EA011680130602607\n");
}

test "signed extension field" {
    try expectRoundTrip("I023638VAT3940SIU" ++
        "\nB1159204549164N00614910EA0117001308-0207" ++
        "\nB1159214549168N00614909EA01168013060.2607\n"); // "0.26" is not a number field value
    try expectRoundTrip("I023638VAT3940SIU" ++
        "\nB1159204549164N00614910EA0117001308-0207" ++
        "\nB1159214549168N00614909EA0116801306+0207" ++ // "+02" would not rebuild: stays text
        "\nB1159224549168N00614909EA0116801306-1107\n");
}

test "fixes missing their last fields" {
    try expectRoundTrip(header ++
        "\nB1159204549164N00614910EA011700130802507" ++
        "\nB1159214549168N00614909EA011680130602" ++
        "\nB1159224549168N00614909EA0116801306" ++
        "\nB1159234549168N00614909EA011680130602508\n");
}

test "tail longer than the I-record" {
    try expectRoundTrip("I023638FXA3940SIU" ++
        "\nB1045174501032N00104240EA002250022500500600200010813508801234" ++
        "\nB1045184501032N00104240EA002250022500500400320001081023744\n");
}

test "many blank lines are cheap" {
    const gpa = testing.allocator;
    var igc: std.ArrayList(u8) = .empty;
    defer igc.deinit(gpa);
    for (0..10) |m| for (0..60) |s| {
        try igc.print(gpa, "B11{d:0>2}{d:0>2}4549164N00614910EA0117001308\n\n\n\n", .{ m, s });
    };
    try expectRoundTrip(igc.items);
    try testing.expect(try encodedLen(igc.items) < 300); // 600 fixes and 1,800 blank lines
}

test "tails that fit no field layout" {
    try expectRoundTrip(header ++
        "\nB1159204549164N00614910EA011700130802507" ++
        "\nB1159214549168N00614909EA0116801306025" ++
        "\nB1159224549168N00614909EA01168013060\n");
}

test "extensions of different lengths" {
    try expectRoundTrip(header ++
        "\nB1159204549164N00614910EA011700130802507" ++
        "\nB1159214549168N00614909EA0116801306\n");
}

test "no fixes" {
    try expectRoundTrip(header ++ "\n");
    try expectRoundTrip("");
}

test "lone line feed inside a crlf file" {
    try expectRoundTrip("AXSR\r\nHFDTE130621\r\nI023638TAS3940SIU" ++
        "\r\nLXSRcomment with a lone\nline feed" ++
        "\r\nB1159204549164N00614910EA011700130802507\r\n\r\n");
}

test "common headers compress well" {
    const headers = "HFDTM100GPSDATUM:WGS-1984\nHFFXA035\nHFPLTPILOTINCHARGE:\nHFGIDGLIDERID:\n";
    try testing.expect(try encodedLen(headers) < headers.len / 2);
}

test "non-ascii header" {
    try expectRoundTrip("HFPLTPILOT:J\xe9r\xf4me\nB1159204549164N00614910EA0117001308\n");
}

test "small step back in time is kept" {
    try expectRoundTrip(header ++
        "\nB1159204549164N00614910EA011700130802507" ++
        "\nB1159084549168N00614909EA011680130602607\n");
}

test "midnight rollover is refused" {
    try testing.expectError(error.MidnightRollover, encode(testing.allocator, "B2359594549164N00614910EA0117001308\n" ++
        "B0000004549168N00614909EA0116801306\n"));
}

test "negative altitudes" {
    try expectRoundTrip(header ++
        "\nB1159202109191S05516887EA-0001-000302507" ++
        "\nB1159212109191S05516884EA00002-000102607\n");
}

test "not an IGCZ file" {
    try testing.expectError(error.NotIgcz, decode(testing.allocator, "B115920"));
    try testing.expectError(error.UnsupportedVersion, decode(testing.allocator, "IGCZ\x05"));
}

test "damaged files are errors, not crashes" {
    const gpa = testing.allocator;
    const encoded = try encode(gpa, header ++
        "\nB1159204549164N00614910EA011700130802507" ++
        "\nB1159214549168N00614909EA011680130602607\n");
    defer gpa.free(encoded);
    // Every truncation and every single-byte change must decode or fail cleanly.
    for (magic.len + 1..encoded.len) |n| {
        if (decode(gpa, encoded[0..n])) |d| gpa.free(d) else |_| {}
    }
    const copy = try gpa.dupe(u8, encoded);
    defer gpa.free(copy);
    for (magic.len + 1..copy.len) |i| {
        for ([_]u8{ 0x00, 0x01, 0x7F, 0x80, 0xFF }) |b| {
            const saved = copy[i];
            copy[i] = b;
            if (decode(gpa, copy)) |d| gpa.free(d) else |_| {}
            copy[i] = saved;
        }
    }
}
