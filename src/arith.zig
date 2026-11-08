//! Adaptive arithmetic coding for number columns (the same design as LZMA's range coder).
//!
//! The coder only stores yes/no answers, each with a probability it learns as it goes. A number
//! (the error of a guess) becomes a short series of questions:
//!
//!   1. was the guess exact?
//!   2. was the error negative?
//!   3. how many binary digits does it have?   (one "more digits?" at a time)
//!   4. what are those digits?
//!
//! Each question has its own probabilities, with a separate set per "context": how big the last
//! two errors were, so calm and turbulent stretches of a flight each learn their own odds.

const std = @import("std");

const prob_bits = 11;
const prob_one: u16 = 1 << prob_bits;
pub const prob_init: u16 = prob_one / 2;
/// How fast probabilities follow the data: each answer moves them 1/32 of the way.
const move = 5;
const top: u32 = 1 << 24;

/// Size of the last two errors: 0, 1, 2-3, 4-7, ..., 64+.
const contexts = 8;
const max_digits = 64;
/// Digits after the leading 1 that get learned odds; later ones cost exactly one bit.
const learned_digits = 6;

pub const Encoder = struct {
    low: u64 = 0,
    range: u32 = 0xFFFF_FFFF,
    cache: u8 = 0,
    cache_size: u64 = 1,
    out: std.ArrayList(u8) = .empty,
    gpa: std.mem.Allocator,

    pub fn init(gpa: std.mem.Allocator) Encoder {
        return .{ .gpa = gpa };
    }

    pub fn deinit(e: *Encoder) void {
        e.out.deinit(e.gpa);
    }

    fn shiftLow(e: *Encoder) error{OutOfMemory}!void {
        const low = e.low;
        if (low < 0xFF00_0000 or low > 0xFFFF_FFFF) {
            // The top byte is final (or a carry just made it final): write it and any pending 0xFF bytes.
            const carry: u8 = @intCast(low >> 32);
            var temp = e.cache;
            while (true) {
                try e.out.append(e.gpa, temp +% carry);
                temp = 0xFF;
                e.cache_size -= 1;
                if (e.cache_size == 0) break;
            }
            e.cache = @truncate(low >> 24);
        }
        e.cache_size += 1;
        e.low = (low & 0x00FF_FFFF) << 8;
    }

    fn normalize(e: *Encoder) error{OutOfMemory}!void {
        while (e.range < top) {
            e.range <<= 8;
            try e.shiftLow();
        }
    }

    /// Stores one yes/no answer using (then updating) the probability `p`.
    pub fn bit(e: *Encoder, p: *u16, b: bool) error{OutOfMemory}!void {
        const bound = (e.range >> prob_bits) * p.*;
        if (b) {
            e.low += bound;
            e.range -= bound;
            p.* -= p.* >> move;
        } else {
            e.range = bound;
            p.* += (prob_one - p.*) >> move;
        }
        try e.normalize();
    }

    /// Stores bits that are as likely 0 as 1: exactly one bit each.
    pub fn direct(e: *Encoder, value: u64, nbits: u6) error{OutOfMemory}!void {
        var k: u6 = nbits;
        while (k > 0) {
            k -= 1;
            e.range >>= 1;
            if ((value >> k) & 1 != 0) e.low += e.range;
            try e.normalize();
        }
    }

    /// Flushes the pending bytes. The encoder must not be used afterwards.
    pub fn finish(e: *Encoder) error{OutOfMemory}![]const u8 {
        for (0..5) |_| try e.shiftLow();
        return e.out.items;
    }
};

pub const Decoder = struct {
    data: []const u8,
    pos: usize,
    range: u32 = 0xFFFF_FFFF,
    code: u64,

    pub fn init(data: []const u8, pos: usize) Decoder {
        var d: Decoder = .{ .data = data, .pos = pos, .code = 0 };
        for (0..5) |_| d.code = (d.code << 8) | d.nextByte();
        return d;
    }

    /// Past the end the stream reads as zeros, like the encoder's implicit padding.
    fn nextByte(d: *Decoder) u8 {
        const b = if (d.pos < d.data.len) d.data[d.pos] else 0;
        d.pos += 1;
        return b;
    }

    fn normalize(d: *Decoder) void {
        while (d.range < top) {
            d.range <<= 8;
            d.code = ((d.code << 8) | d.nextByte()) & 0xFFFF_FFFF;
        }
    }

    pub fn bit(d: *Decoder, p: *u16) bool {
        const bound = (d.range >> prob_bits) * p.*;
        const b = d.code >= bound;
        if (b) {
            d.code -= bound;
            d.range -= bound;
            p.* -= p.* >> move;
        } else {
            d.range = bound;
            p.* += (prob_one - p.*) >> move;
        }
        d.normalize();
        return b;
    }

    pub fn direct(d: *Decoder, nbits: u6) u64 {
        var value: u64 = 0;
        for (0..nbits) |_| {
            d.range >>= 1;
            const b = d.code >= d.range;
            if (b) d.code -= d.range;
            value = (value << 1) | @intFromBool(b);
            d.normalize();
        }
        return value;
    }
};

/// The learned probabilities for one column.
const Model = struct {
    exact: [contexts]u16 = @splat(prob_init),
    negative: [contexts * 3]u16 = @splat(prob_init),
    more_digits: [contexts][max_digits]u16 = @splat(@splat(prob_init)),
    /// One small binary tree of learned odds per digit count (only the first 2^(learned+1) nodes are used).
    digits: [max_digits][2 << learned_digits]u16 = @splat(@splat(prob_init)),
};

fn bitLength(x: u64) u7 {
    return 64 - @as(u7, @clz(x));
}

fn context(e1: i64, e2: i64) usize {
    return @min(bitLength(@abs(e1) +| @abs(e2)), contexts - 1);
}

fn signContext(ctx: usize, e1: i64) usize {
    return ctx * 3 + @as(usize, if (e1 < 0) 0 else if (e1 == 0) 1 else 2);
}

fn writeError(enc: *Encoder, m: *Model, e: i64, e1: i64, e2: i64) error{ OutOfMemory, NumberTooLarge }!void {
    const ctx = context(e1, e2);
    try enc.bit(&m.exact[ctx], e != 0);
    if (e == 0) return;
    try enc.bit(&m.negative[signContext(ctx, e1)], e < 0);

    const magnitude = @abs(e);
    const n = bitLength(magnitude);
    if (n >= max_digits) return error.NumberTooLarge;
    const more = &m.more_digits[ctx];
    for (0..n - 1) |i| try enc.bit(&more[i], true);
    try enc.bit(&more[n - 1], false);

    // The leading digit is always 1: send the n-1 digits after it.
    const learned: u6 = @intCast(@min(n - 1, learned_digits));
    const tree = &m.digits[n];
    var node: usize = 1;
    var k: u6 = @intCast(n - 1);
    for (0..learned) |_| {
        k -= 1;
        const b: u1 = @truncate(magnitude >> k);
        try enc.bit(&tree[node], b != 0);
        node = node * 2 + b;
    }
    const rest: u6 = @intCast(n - 1 - learned);
    if (rest > 0) try enc.direct(magnitude & ((@as(u64, 1) << rest) - 1), rest);
}

fn readError(dec: *Decoder, m: *Model, e1: i64, e2: i64) error{Corrupt}!i64 {
    const ctx = context(e1, e2);
    if (!dec.bit(&m.exact[ctx])) return 0;
    const negative = dec.bit(&m.negative[signContext(ctx, e1)]);

    const more = &m.more_digits[ctx];
    var n: usize = 1;
    while (dec.bit(&more[n - 1])) {
        n += 1;
        if (n >= max_digits) return error.Corrupt;
    }

    const learned = @min(n - 1, learned_digits);
    const tree = &m.digits[n];
    var node: usize = 1;
    for (0..learned) |_| node = node * 2 + @intFromBool(dec.bit(&tree[node]));
    var magnitude: u64 = node; // the leading 1 plus the learned digits
    const rest: u6 = @intCast(n - 1 - learned);
    if (rest > 0) magnitude = (magnitude << rest) | dec.direct(rest);
    const signed: i64 = @intCast(magnitude); // n <= 63 digits always fits
    return if (negative) -signed else signed;
}

/// `slope` guesses "same change as last time"; otherwise the guess is "same value as last time".
pub fn writeColumn(enc: *Encoder, values: []const i64, slope: bool) error{ OutOfMemory, NumberTooLarge }!void {
    const m = try enc.gpa.create(Model);
    defer enc.gpa.destroy(m);
    m.* = .{};
    var prev: i64 = 0;
    var prev_change: i64 = 0;
    var e1: i64 = 0;
    var e2: i64 = 0;
    for (values) |v| {
        const change = std.math.sub(i64, v, prev) catch return error.NumberTooLarge;
        const e = std.math.sub(i64, change, if (slope) prev_change else 0) catch return error.NumberTooLarge;
        try writeError(enc, m, e, e1, e2);
        e2 = e1;
        e1 = e;
        prev_change = change;
        prev = v;
    }
}

pub fn readColumn(gpa: std.mem.Allocator, dec: *Decoder, count: usize, slope: bool) error{ OutOfMemory, Corrupt }![]i64 {
    const m = try gpa.create(Model);
    defer gpa.destroy(m);
    m.* = .{};
    const values = try gpa.alloc(i64, count);
    errdefer gpa.free(values);
    var prev: i64 = 0;
    var prev_change: i64 = 0;
    var e1: i64 = 0;
    var e2: i64 = 0;
    for (values) |*v| {
        const e = try readError(dec, m, e1, e2);
        const guess = std.math.add(i64, prev, if (slope) prev_change else 0) catch return error.Corrupt;
        v.* = std.math.add(i64, guess, e) catch return error.Corrupt;
        e2 = e1;
        e1 = e;
        prev_change = std.math.sub(i64, v.*, prev) catch return error.Corrupt;
        prev = v.*;
    }
    return values;
}

const testing = std.testing;

test "columns round trip" {
    const gpa = testing.allocator;
    var big: [3000]i64 = undefined;
    for (&big, 0..) |*v, i| v.* = @as(i64, @intCast((i * 7919) % 1000)) - 500;
    const cases = [_][]const i64{
        &.{},
        &.{0},
        &(.{5} ** 200),
        &.{ 2_749_164, 2_749_168, 2_749_171, -5_000_000, 0, 3, 1 << 40, -(1 << 40) },
        &big,
    };

    var enc: Encoder = .init(gpa);
    defer enc.deinit();
    for (cases) |values| {
        try writeColumn(&enc, values, true);
        try writeColumn(&enc, values, false);
    }
    try enc.direct(0b1011, 4); // a marker right after, to check the decoder stays in step
    const data = try enc.finish();

    var dec: Decoder = .init(data, 0);
    for (cases) |values| {
        for ([_]bool{ true, false }) |slope| {
            const got = try readColumn(gpa, &dec, values.len, slope);
            defer gpa.free(got);
            try testing.expectEqualSlices(i64, values, got);
        }
    }
    try testing.expectEqual(0b1011, dec.direct(4));
}

test "carry heavy stream" {
    // Answers that go against the learned odds push carries through runs of 0xFF bytes.
    const gpa = testing.allocator;
    const n = 53_000;
    const answers = try gpa.alloc(bool, n);
    defer gpa.free(answers);
    for (answers, 0..) |*a, i| a.* = if (i < 50_000) ((i * 2654435761) >> 7) % 97 == 0 else true;

    var enc: Encoder = .init(gpa);
    defer enc.deinit();
    var p = prob_init;
    for (answers) |a| try enc.bit(&p, a);
    var dec: Decoder = .init(try enc.finish(), 0);
    p = prob_init;
    for (answers) |a| try testing.expectEqual(a, dec.bit(&p));
}

test "exact guesses are nearly free" {
    const gpa = testing.allocator;
    var values: [10_000]i64 = undefined;
    for (&values, 0..) |*v, i| v.* = @intCast(i * 3);
    var enc: Encoder = .init(gpa);
    defer enc.deinit();
    try writeColumn(&enc, &values, true);
    try testing.expect((try enc.finish()).len < 100);
}

test "too large numbers are refused" {
    var enc: Encoder = .init(testing.allocator);
    defer enc.deinit();
    // The second error is minInt(i64): 64 binary digits.
    try testing.expectError(error.NumberTooLarge, writeColumn(&enc, &.{ std.math.maxInt(i64), -1 }, false));
}
