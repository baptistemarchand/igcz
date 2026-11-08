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

    // The browser converter: `zig build wasm` writes web/igcz.wasm next to the page.
    const wasm_target = b.resolveTargetQuery(.{ .cpu_arch = .wasm32, .os_tag = .freestanding });
    const wasm_igcz = b.createModule(.{
        .root_source_file = b.path("src/igcz.zig"),
        .target = wasm_target,
        .optimize = .ReleaseSmall,
    });
    wasm_igcz.addAnonymousImport("primer", .{ .root_source_file = b.path("experiments/primer.txt") });
    const wasm = b.addExecutable(.{
        .name = "igcz",
        .root_module = b.createModule(.{
            .root_source_file = b.path("src/wasm.zig"),
            .target = wasm_target,
            .optimize = .ReleaseSmall,
            .imports = &.{.{ .name = "igcz", .module = wasm_igcz }},
        }),
    });
    wasm.entry = .disabled;
    wasm.rdynamic = true;
    const update_web = b.addUpdateSourceFiles();
    update_web.addCopyFileToSource(wasm.getEmittedBin(), "web/igcz.wasm");
    b.step("wasm", "Build web/igcz.wasm for the browser converter").dependOn(&update_web.step);

    const tests = b.addTest(.{ .root_module = igcz });
    b.step("test", "Run the unit tests").dependOn(&b.addRunArtifact(tests).step);
}
