--!strict
local workflow = require("../workflow")

local function host(id: number, panes: { number }, address: string)
	return { id = id, name = address, kind = "remote", state = "connected", address = address, panes = panes, sessions = {} }
end

local first = host(1, { 11 }, "first")
local second = host(2, { 22 }, "second")
assert(workflow.host_for_pane({ first, second }, 22) == second, "Destination must follow pane membership, not the first/current host")
assert(workflow.host_for_pane({ first }, 22) == nil, "Unknown pane must not upload to a fallback host")

local pane = { pane = 22, title = "work", program = "/bin/bash", running = { line = "omp", ms = 10 } }
local destination = { pane = 22, host = 2, address = "second", program = "/bin/bash", running = "omp", agent = true }
assert(workflow.same_destination(destination, second, pane, true))
assert(not workflow.same_destination(destination, first, pane, true), "Wrong host must not receive a paste")
second.state = "reconnecting"
assert(not workflow.same_destination(destination, second, pane, true), "Disconnected host must not receive a paste")
second.state = "connected"
second.address = "different"
assert(not workflow.same_destination(destination, second, pane, true), "Reused host slot must not receive a paste")
second.address = "second"
assert(not workflow.same_destination(destination, second, nil, true), "Closed pane must not receive a paste")
pane.running.line = "vim"
assert(not workflow.same_destination(destination, second, pane, true), "Changed program must not receive a paste")
pane.running.line = "omp"
assert(not workflow.same_destination(destination, second, pane, false), "Exited agent must not receive a paste")

assert(workflow.format_paths({ "/home/u/file with č.txt" }, "omp") == '@"/home/u/file with č.txt" ')
assert(workflow.format_paths({ "/home/u/a'b.txt", "/home/u/$(touch injected)" }, "paths") == "'/home/u/a'\\''b.txt' '/home/u/$(touch injected)' ")
assert(not pcall(workflow.format_paths, { "/tmp/file\nexecute" }, "paths"), "Newlines must not be typed into a program")
assert(not pcall(workflow.format_paths, { "relative.txt" }, "omp"), "Local/relative paths must not be reported as remote uploads")
assert(not pcall(workflow.format_paths, {}, "paths"))
print("PASS: host selection, stale-destination safety, shell quoting, and OMP file references")
