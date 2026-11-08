const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{});

    const igcz = b.addModule("igcz", .{
        .root_source_file = b.path("src/igcz.zig"),
        .target = target,
        .optimize = optimize,
    });
    // Shared with the Python reference implementation: one primer, one format.
    igcz.addAnonymousImport("primer", .{ .root_source_file = b.path("experiments/primer.txt") });

    const exe = b.addExecutable(.{
        .name = "igcz",
        .root_module = b.createModule(.{
            .root_source_file = b.path("src/main.zig"),
            .target = target,
            .optimize = optimize,
            .imports = &.{.{ .name = "igcz", .module = igcz }},
        }),
    });
    b.installArtifact(exe);

    const run = b.addRunArtifact(exe);
    if (b.args) |args| run.addArgs(args);
    b.step("run", "Run the igcz command line tool").dependOn(&run.step);

    const tests = b.addTest(.{ .root_module = igcz });
    b.step("test", "Run the unit tests").dependOn(&b.addRunArtifact(tests).step);
}
