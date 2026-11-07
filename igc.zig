const std = @import("std");

const Fix = struct {
    time: u32,
    lat: i32,
    lng: i32,
    alt: u32,

    const size = 16;

    fn decode(bytes: *const [size]u8) Fix {
        return .{
            .time = std.mem.readInt(u32, bytes[0..4], .big),
            .lat = std.mem.readInt(i32, bytes[4..8], .big),
            .lng = std.mem.readInt(i32, bytes[8..12], .big),
            .alt = std.mem.readInt(u32, bytes[12..16], .big),
        };
    }
};

/// One coordinate rendered the way an IGC B-record wants it:
/// latitude  as DDMMmmm + N/S   (e.g. 4549070N)
/// longitude as DDDMMmmm + E/W  (e.g. 00614868E)
///
/// `mmm` is THOUSANDTHS OF A MINUTE, not seconds.
const Coord = struct {
    degrees: u32,
    /// Minutes x 1000, so 49.070 minutes is stored as 49_070. Always < 60_000.
    minutes: u32,
    hemisphere: u8,

    /// `value` is in units of 1e-7 degrees, the way Fix stores it.
    fn from(value: i32, positive: u8, negative: u8) Coord {
        const abs: u64 = @abs(value);

        const units_per_degree = 10_000_000;
        // remainder * 60 is in 1e-7 minutes; one thousandth of a minute is 1e4 of those.
        const units_per_milli_minute = 10_000;

        var degrees: u64 = abs / units_per_degree;
        const remainder: u64 = abs % units_per_degree;

        // Adding half the divisor rounds to nearest instead of truncating.
        var minutes: u64 = (remainder * 60 + units_per_milli_minute / 2) / units_per_milli_minute;

        // Rounding can reach 60.000 minutes, which is the next whole degree.
        if (minutes >= 60_000) {
            minutes -= 60_000;
            degrees += 1;
        }

        return .{
            .degrees = @intCast(degrees),
            .minutes = @intCast(minutes),
            .hemisphere = if (value < 0) negative else positive,
        };
    }

    fn lat(value: i32) Coord {
        return from(value, 'N', 'S');
    }

    fn lng(value: i32) Coord {
        return from(value, 'E', 'W');
    }
};

pub fn main() !void {
    var da: std.heap.DebugAllocator(.{}) = .init;
    defer {
        if (da.deinit() == .leak) {
            std.debug.print("MEMORY LEAKED!\n", .{});
        }
    }
    const allocator = da.allocator();

    var threaded = std.Io.Threaded.init(allocator, .{});
    defer threaded.deinit();
    const io = threaded.io();

    const cwd = std.Io.Dir.cwd();

    const file = try cwd.openFile(io, "test.igcc", .{});
    defer file.close(io);

    var buffer: [64 * Fix.size]u8 = undefined;
    var file_reader = file.reader(io, &buffer);
    const reader = &file_reader.interface;

    var count: usize = 0;
    while (true) {
        const bytes = reader.takeArray(Fix.size) catch |err| switch (err) {
            // Either a clean end of file, or a trailing partial record.
            error.EndOfStream => {
                if (reader.bufferedLen() != 0) return error.TruncatedRecord;
                break;
            },
            else => |e| return e,
        };
        const fix = Fix.decode(bytes);
        count += 1;

        const d: std.time.epoch.DaySeconds = .{ .secs = @intCast(fix.time % std.time.epoch.secs_per_day) };
        const hours = d.getHoursIntoDay();
        const minutes = d.getMinutesIntoHour();
        const seconds = d.getSecondsIntoMinute();

        const debug_delimiter = " ";

        const lat = Coord.lat(fix.lat);
        const lng = Coord.lng(fix.lng);

        std.debug.print("B{s}{d:0>2}{d:0>2}{d:0>2}{s}{d:0>2}{d:0>5}{c}{s}{d:0>3}{d:0>5}{c}{s}{d}\n", .{
            debug_delimiter,
            hours,
            minutes,
            seconds,
            debug_delimiter,
            lat.degrees,
            lat.minutes,
            lat.hemisphere,
            debug_delimiter,
            lng.degrees,
            lng.minutes,
            lng.hemisphere,
            debug_delimiter,
            fix.alt,
        });

        std.debug.print("{d}: t={d} lat={d}.{d:0>7} lng={d}.{d:0>7} alt={d}\n", .{
            count,
            fix.time,
            @divTrunc(fix.lat, 10_000_000),
            @abs(@rem(fix.lat, 10_000_000)),
            @divTrunc(fix.lng, 10_000_000),
            @abs(@rem(fix.lng, 10_000_000)),
            fix.alt,
        });
    }

    std.debug.print("{d} fixes read\n", .{count});
}
