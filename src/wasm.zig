//! WebAssembly entry points for the browser converter (web/).
//!
//! The page copies the input into memory from `alloc`, calls `encode` or `decode`, then reads
//! the result through `resultPtr`/`resultLen`. On failure the result holds the error name.

const std = @import("std");
const igcz = @import("igcz");

const gpa = std.heap.wasm_allocator;

var result: []u8 = &.{};

export fn alloc(len: usize) ?[*]u8 {
    const buf = gpa.alloc(u8, len) catch return null;
    return buf.ptr;
}

export fn free(ptr: [*]u8, len: usize) void {
    gpa.free(ptr[0..len]);
}

export fn resultPtr() [*]u8 {
    return result.ptr;
}

export fn resultLen() usize {
    return result.len;
}

/// Returns true on success. The input stays owned by the caller.
export fn encode(ptr: [*]const u8, len: usize) bool {
    return finish(igcz.encode(gpa, ptr[0..len]));
}

export fn decode(ptr: [*]const u8, len: usize) bool {
    return finish(igcz.decode(gpa, ptr[0..len]));
}

export fn version() u32 {
    return igcz.version;
}

fn finish(output: anyerror![]u8) bool {
    if (result.len > 0) gpa.free(result);
    result = &.{};
    if (output) |bytes| {
        result = bytes;
        return true;
    } else |err| {
        // Copied so that `result` is always ours to free.
        result = gpa.dupe(u8, @errorName(err)) catch &.{};
        return false;
    }
}
