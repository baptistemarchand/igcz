//! Whole numbers in as few bytes as possible: zigzag folds the sign in (0, -1, 1, -2, 2... become
//! 0, 1, 2, 3, 4...), then 7 bits per byte with the high bit meaning "more bytes follow".

const std = @import("std");

pub fn zigzag(n: i64) u64 {
    return @bitCast((n << 1) ^ (n >> 63));
}

pub fn unzigzag(z: u64) i64 {
    return @as(i64, @bitCast(z >> 1)) ^ -@as(i64, @intCast(z & 1));
}

pub fn write(list: *std.ArrayList(u8), gpa: std.mem.Allocator, n: i64) error{OutOfMemory}!void {
    var x = zigzag(n);
    while (x >= 0x80) : (x >>= 7) {
        try list.append(gpa, @as(u8, @truncate(x)) | 0x80);
    }
    try list.append(gpa, @intCast(x));
}

/// Reads numbers written by `write` from a byte slice.
pub const Reader = struct {
    data: []const u8,
    pos: usize,

    pub fn num(r: *Reader) error{Corrupt}!i64 {
        var x: u64 = 0;
        var shift: u7 = 0;
        while (true) {
            if (r.pos >= r.data.len or shift >= 64) return error.Corrupt;
            const byte = r.data[r.pos];
            r.pos += 1;
            x |= @as(u64, byte & 0x7F) << @intCast(shift);
            if (byte < 0x80) return unzigzag(x);
            shift += 7;
        }
    }

    /// A number that must fit in a count or a length.
    pub fn len(r: *Reader) error{Corrupt}!usize {
        const n = try r.num();
        if (n < 0) return error.Corrupt;
        return std.math.cast(usize, n) orelse error.Corrupt;
    }

    pub fn bytes(r: *Reader, size: usize) error{Corrupt}![]const u8 {
        if (size > r.data.len - r.pos) return error.Corrupt;
        defer r.pos += size;
        return r.data[r.pos..][0..size];
    }
};

test "round trip" {
    const gpa = std.testing.allocator;
    const cases = [_]i64{ 0, 1, -1, 63, -64, 64, -65, 127, 128, 1_000_000_000, -1_000_000_000, std.math.maxInt(i64), std.math.minInt(i64) };
    for (cases) |n| {
        var list: std.ArrayList(u8) = .empty;
        defer list.deinit(gpa);
        try write(&list, gpa, n);
        var r: Reader = .{ .data = list.items, .pos = 0 };
        try std.testing.expectEqual(n, try r.num());
        try std.testing.expectEqual(list.items.len, r.pos);
    }
}

test "small numbers take one byte" {
    const gpa = std.testing.allocator;
    var n: i64 = -64;
    while (n < 64) : (n += 1) {
        var list: std.ArrayList(u8) = .empty;
        defer list.deinit(gpa);
        try write(&list, gpa, n);
        try std.testing.expectEqual(1, list.items.len);
    }
}

test "truncated input is an error" {
    var r: Reader = .{ .data = &.{0x80}, .pos = 0 };
    try std.testing.expectError(error.Corrupt, r.num());
}
