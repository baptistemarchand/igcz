//! B-records (one GPS fix per line) to numbers and back.
//!
//! Layout: B HHMMSS DDMMmmm N|S DDDMMmmm E|W A|V PPPPP GGGGG <extensions>
//! Coordinates are kept in thousandths of a minute, exactly as written, so nothing is lost.

const std = @import("std");

/// Index in a B-line where the extension fields begin.
pub const tail_start = 35;

pub const Fix = struct {
    /// Seconds since midnight.
    time: i64,
    /// Thousandths of a minute, negative = south.
    lat: i64,
    /// Thousandths of a minute, negative = west.
    lng: i64,
    /// 'A' (3D fix) or 'V' (2D fix or no GPS).
    valid: u8,
    /// Metres.
    pressure_alt: i64,
    /// Metres.
    gnss_alt: i64,
    /// Extension fields, as raw text.
    tail: []const u8,
};

/// The fix for a regular B-line, or null if the line must be kept as raw text: anything that
/// would not come back identical once formatted again (odd validity char, minutes >= 60...).
pub fn parse(line: []const u8) ?Fix {
    if (line.len < tail_start or line[0] != 'B') return null;
    const fix: Fix = .{
        .time = (int(line[1..3]) orelse return null) * 3600 +
            (int(line[3..5]) orelse return null) * 60 +
            (int(line[5..7]) orelse return null),
        .lat = signed(
            (int(line[7..9]) orelse return null) * 60_000 + (int(line[9..14]) orelse return null),
            line[14] == 'S',
        ),
        .lng = signed(
            (int(line[15..18]) orelse return null) * 60_000 + (int(line[18..23]) orelse return null),
            line[23] == 'W',
        ),
        .valid = line[24],
        .pressure_alt = int(line[25..30]) orelse return null,
        .gnss_alt = int(line[30..35]) orelse return null,
        .tail = line[tail_start..],
    };
    if (fix.valid != 'A' and fix.valid != 'V') return null;

    var buf: [256]u8 = undefined;
    var w: std.Io.Writer = .fixed(&buf);
    writeHead(&w, fix) catch unreachable; // 7 numbers of at most 20 digits each fit
    if (!std.mem.eql(u8, w.buffered(), line[0..tail_start])) return null;
    return fix;
}

fn signed(value: i64, negative: bool) i64 {
    return if (negative) -value else value;
}

/// Digits with an optional leading minus: the only shapes `writePadded` produces.
fn int(s: []const u8) ?i64 {
    const digits = if (s.len > 0 and s[0] == '-') s[1..] else s;
    if (digits.len == 0) return null;
    for (digits) |c| if (!std.ascii.isDigit(c)) return null;
    const value = std.fmt.parseInt(i64, digits, 10) catch return null;
    return if (digits.len < s.len) -value else value;
}

/// Writes the whole B-line.
pub fn write(w: *std.Io.Writer, fix: Fix) std.Io.Writer.Error!void {
    try writeHead(w, fix);
    try w.writeAll(fix.tail);
}

/// Everything before the extensions: 35 characters for any fix read from a regular B-line.
fn writeHead(w: *std.Io.Writer, fix: Fix) std.Io.Writer.Error!void {
    const hours = @divFloor(fix.time, 3600);
    const rest = @mod(fix.time, 3600);
    try w.writeByte('B');
    try writePadded(w, hours, 2);
    try writePadded(w, @divFloor(rest, 60), 2);
    try writePadded(w, @mod(rest, 60), 2);
    try writeCoord(w, fix.lat, 2, "NS");
    try writeCoord(w, fix.lng, 3, "EW");
    try w.writeByte(fix.valid);
    try writePadded(w, fix.pressure_alt, 5);
    try writePadded(w, fix.gnss_alt, 5);
}

fn writeCoord(w: *std.Io.Writer, value: i64, degree_width: usize, hemispheres: *const [2]u8) std.Io.Writer.Error!void {
    const abs = @abs(value);
    try writeDigits(w, abs / 60_000, degree_width);
    try writeDigits(w, abs % 60_000, 5);
    try w.writeByte(hemispheres[@intFromBool(value < 0)]);
}

/// Zero-padded to `width` characters, the sign included: -34 in 5 gives "-0034". Wider numbers
/// are written in full.
pub fn writePadded(w: *std.Io.Writer, value: i64, width: usize) std.Io.Writer.Error!void {
    if (value < 0) {
        try w.writeByte('-');
        return writeDigits(w, @abs(value), width -| 1);
    }
    return writeDigits(w, @abs(value), width);
}

fn writeDigits(w: *std.Io.Writer, value: u64, width: usize) std.Io.Writer.Error!void {
    var buf: [20]u8 = undefined;
    const digits = std.fmt.bufPrint(&buf, "{d}", .{value}) catch unreachable;
    try w.splatByteAll('0', width -| digits.len);
    try w.writeAll(digits);
}

const testing = std.testing;

fn expectRoundTrip(line: []const u8) !void {
    const fix = parse(line) orelse return error.TestUnexpectedResult;
    var buf: [256]u8 = undefined;
    var w: std.Io.Writer = .fixed(&buf);
    try write(&w, fix);
    try testing.expectEqualStrings(line, w.buffered());
}

test "regular lines round trip" {
    try expectRoundTrip("B1159204549164N00614910EA011700130802507");
    try expectRoundTrip("B1159203349164S07014910WV011700130802507");
    try expectRoundTrip("B1159202109191S05516887EA-0001-000302507");
}

test "numbers" {
    const fix = parse("B1159203349164S07014910WV-00170130802507").?;
    try testing.expectEqual(11 * 3600 + 59 * 60 + 20, fix.time);
    try testing.expectEqual(-(33 * 60_000 + 49_164), fix.lat);
    try testing.expectEqual(-(70 * 60_000 + 14_910), fix.lng);
    try testing.expectEqual('V', fix.valid);
    try testing.expectEqual(-17, fix.pressure_alt);
    try testing.expectEqual(1308, fix.gnss_alt);
    try testing.expectEqualStrings("02507", fix.tail);
}

test "lines that would not come back identical are refused" {
    const refused = [_][]const u8{
        "B11592",
        "B1159214569999N00614909EA011680130602607", // 69.999 minutes
        "B1159224549168N00614909EX011680130602607", // unknown validity
        "B1159224549168X00614909EA011680130602607", // unknown hemisphere
        "B1159220000000S00614909EA011680130602607", // south of zero
        "B11 9224549168N00614909EA011680130602607",
        "B1159224549168N00614909EA+11680130602607",
        "B1159224549168N00614909EA-0000130602607",
        "H1159224549168N00614909EA011680130602607",
    };
    for (refused) |line| try testing.expectEqual(null, parse(line));
}

test "padding keeps the sign inside the width" {
    var buf: [32]u8 = undefined;
    var w: std.Io.Writer = .fixed(&buf);
    try writePadded(&w, -34, 5);
    try writePadded(&w, 7, 3);
    try writePadded(&w, -5, 2);
    try writePadded(&w, 123456, 2);
    try testing.expectEqualStrings("-0034007-5123456", w.buffered());
}
