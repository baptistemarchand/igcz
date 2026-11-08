const std = @import("std");
const igcz = @import("igcz");

const usage =
    \\usage:
    \\  igcz encode <in.igc> <out.igcz>
    \\  igcz decode <in.igcz> <out.igc>
    \\  igcz check <file or folder>...   round trip every file (.igc, or .gz holding an .igc),
    \\                                   then print sizes and speed
    \\
;

pub fn main(init: std.process.Init) !u8 {
    const gpa = init.gpa;
    const io = init.io;
    const args = try init.minimal.args.toSlice(init.arena.allocator());
    const cwd = std.Io.Dir.cwd();

    if (args.len == 4 and (std.mem.eql(u8, args[1], "encode") or std.mem.eql(u8, args[1], "decode"))) {
        const input = try cwd.readFileAlloc(io, args[2], gpa, .unlimited);
        defer gpa.free(input);
        const output = if (args[1][0] == 'e')
            igcz.encode(gpa, input) catch |err| return fail(args[2], err)
        else
            igcz.decode(gpa, input) catch |err| return fail(args[2], err);
        defer gpa.free(output);
        try cwd.writeFile(io, .{ .sub_path = args[3], .data = output });
        return 0;
    }
    if (args.len >= 3 and std.mem.eql(u8, args[1], "check")) {
        var stats: Stats = .{};
        for (args[2..]) |path| {
            const stat = try cwd.statFile(io, path, .{});
            if (stat.kind != .directory) {
                check(gpa, io, cwd, path, &stats);
                continue;
            }
            var dir = try cwd.openDir(io, path, .{ .iterate = true });
            defer dir.close(io);
            var it = dir.iterate();
            while (try it.next(io)) |entry| {
                if (entry.kind == .file) check(gpa, io, dir, entry.name, &stats);
            }
        }
        stats.print();
        return if (stats.failed > 0) 1 else 0;
    }
    std.debug.print("{s}", .{usage});
    return 2;
}

fn fail(path: []const u8, err: anyerror) u8 {
    std.debug.print("ERROR {s}: {t}\n", .{ path, err });
    return 1;
}

const Stats = struct {
    ok: usize = 0,
    refused: usize = 0,
    failed: usize = 0,
    igc_bytes: u64 = 0,
    igcz_bytes: u64 = 0,
    encode_ns: i96 = 0,
    decode_ns: i96 = 0,

    fn print(s: Stats) void {
        const mb = @as(f64, @floatFromInt(s.igc_bytes)) / 1e6;
        std.debug.print(
            \\{d} converted, {d} refused, {d} failed
            \\IGC  {d:.2} MB
            \\IGCZ {d:.2} MB ({d:.1}x smaller)
            \\encode {d:.1} MB of IGC per second, decode {d:.1} MB per second (one thread)
            \\
        , .{
            s.ok,
            s.refused,
            s.failed,
            mb,
            @as(f64, @floatFromInt(s.igcz_bytes)) / 1e6,
            @as(f64, @floatFromInt(s.igc_bytes)) / @max(@as(f64, @floatFromInt(s.igcz_bytes)), 1),
            mb / seconds(s.encode_ns),
            mb / seconds(s.decode_ns),
        });
    }

    fn seconds(ns: i96) f64 {
        return @max(@as(f64, @floatFromInt(ns)) / 1e9, 1e-9);
    }
};

/// Encodes then decodes one file, and checks it comes back byte for byte.
fn check(gpa: std.mem.Allocator, io: std.Io, dir: std.Io.Dir, name: []const u8, stats: *Stats) void {
    const raw = readFlight(gpa, io, dir, name) catch |err| {
        stats.failed += 1;
        std.debug.print("UNREADABLE {s}: {t}\n", .{ name, err });
        return;
    };
    defer gpa.free(raw);

    const t0: std.Io.Timestamp = .now(io, .awake);
    const encoded = igcz.encode(gpa, raw) catch |err| {
        if (err == error.MidnightRollover) stats.refused += 1 else stats.failed += 1;
        std.debug.print("{s} {s}: {t}\n", .{ if (err == error.MidnightRollover) "REFUSED" else "FAILED", name, err });
        return;
    };
    defer gpa.free(encoded);
    const t1: std.Io.Timestamp = .now(io, .awake);
    const decoded = igcz.decode(gpa, encoded) catch |err| {
        stats.failed += 1;
        std.debug.print("FAILED {s}: decode {t}\n", .{ name, err });
        return;
    };
    defer gpa.free(decoded);
    const t2: std.Io.Timestamp = .now(io, .awake);

    if (!std.mem.eql(u8, decoded, raw)) {
        stats.failed += 1;
        std.debug.print("FAILED {s}: decoded file differs from the original\n", .{name});
        return;
    }
    stats.ok += 1;
    stats.igc_bytes += raw.len;
    stats.igcz_bytes += encoded.len;
    stats.encode_ns += t0.durationTo(t1).toNanoseconds();
    stats.decode_ns += t1.durationTo(t2).toNanoseconds();
}

fn readFlight(gpa: std.mem.Allocator, io: std.Io, dir: std.Io.Dir, name: []const u8) ![]u8 {
    const data = try dir.readFileAlloc(io, name, gpa, .unlimited);
    if (!std.mem.endsWith(u8, name, ".gz")) return data;
    defer gpa.free(data);
    var in: std.Io.Reader = .fixed(data);
    const window = try gpa.alloc(u8, std.compress.flate.max_window_len);
    defer gpa.free(window);
    var gz: std.compress.flate.Decompress = .init(&in, .gzip, window);
    var out: std.Io.Writer.Allocating = .init(gpa);
    defer out.deinit();
    _ = try gz.reader.streamRemaining(&out.writer);
    return out.toOwnedSlice();
}
