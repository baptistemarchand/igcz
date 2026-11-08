//! Raw deflate with a preset dictionary (the primer: typical header text known to both sides).
//!
//! The standard library has no preset dictionary API, so the primer goes through the stream itself:
//! - compressing: feed the primer, flush to a byte boundary (history is kept), and drop
//!   everything written so far. Later matches may point back into the primer, which is exactly
//!   what a decoder with that dictionary expects (zlib's `zdict`).
//! - decompressing: put the primer in front as a stored (uncompressed) block, and drop it from
//!   the output.

const std = @import("std");
const flate = std.compress.flate;

comptime {
    // A stored block holds at most 65,535 bytes, and matches reach back at most 32 KB.
    std.debug.assert(primer.len <= flate.history_len);
}

/// Part of the format: a new primer needs a new format version, or older files would no longer decode.
pub const primer = @embedFile("primer");

pub fn compress(gpa: std.mem.Allocator, data: []const u8) error{OutOfMemory}![]u8 {
    var out = try std.Io.Writer.Allocating.initCapacity(gpa, 1024);
    defer out.deinit();
    const window = try gpa.alloc(u8, flate.max_window_len);
    defer gpa.free(window);

    var c = flate.Compress.init(&out.writer, window, .raw, .level_9) catch return error.OutOfMemory;
    c.writer.writeAll(primer) catch return error.OutOfMemory;
    c.writer.flush() catch return error.OutOfMemory;
    const primer_end = out.written().len;
    c.writer.writeAll(data) catch return error.OutOfMemory;
    c.finish() catch return error.OutOfMemory;
    return gpa.dupe(u8, out.written()[primer_end..]);
}

pub fn decompress(gpa: std.mem.Allocator, data: []const u8) error{ OutOfMemory, Corrupt }![]u8 {
    const stored_header = 5;
    const input = try gpa.alloc(u8, stored_header + primer.len + data.len);
    defer gpa.free(input);
    input[0] = 0; // not the final block, stored, padded to the byte boundary
    std.mem.writeInt(u16, input[1..3], primer.len, .little);
    std.mem.writeInt(u16, input[3..5], ~@as(u16, primer.len), .little);
    @memcpy(input[stored_header..][0..primer.len], primer);
    @memcpy(input[stored_header + primer.len ..], data);

    var in: std.Io.Reader = .fixed(input);
    const window = try gpa.alloc(u8, flate.max_window_len);
    defer gpa.free(window);
    var d: flate.Decompress = .init(&in, .raw, window);
    var out: std.Io.Writer.Allocating = .init(gpa);
    defer out.deinit();
    _ = d.reader.streamRemaining(&out.writer) catch |err| return switch (err) {
        error.WriteFailed => error.OutOfMemory,
        error.ReadFailed => error.Corrupt,
    };
    return gpa.dupe(u8, out.written()[primer.len..]);
}

const testing = std.testing;

test "round trip" {
    const gpa = testing.allocator;
    const cases = [_][]const u8{ "", "HFDTM100GPSDATUM:WGS-1984\nHFFXA035", "x" ** 100_000 };
    for (cases) |text| {
        const packed_ = try compress(gpa, text);
        defer gpa.free(packed_);
        const back = try decompress(gpa, packed_);
        defer gpa.free(back);
        try testing.expectEqualSlices(u8, text, back);
    }
}

test "common header text costs almost nothing" {
    const packed_ = try compress(testing.allocator, "HFDTM100GPSDATUM:WGS-1984\nHFFXA035\n");
    defer testing.allocator.free(packed_);
    try testing.expect(packed_.len < 12);
}

test "garbage is an error, not a crash" {
    try testing.expectError(error.Corrupt, decompress(testing.allocator, &.{ 0xFF, 0xFF, 0xFF }));
}
