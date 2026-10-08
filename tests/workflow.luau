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

local references = workflow.format_paths({ "/home/u/new upload.txt", "/home/u/second.txt" }, "omp")
local editor = { id = "3.line/input", k = "editor", p = { cursor = 0, sendable = true, text = "" } }
local surface = { surface = { id = "omp", mode = "screen" }, main = { id = "main", k = "col", p = { text = "", sendable = false } }, dock = { id = "dock", k = "col", c = { editor } } }
assert(workflow.composer(nil) == nil, "Missing bootstrap surface must not authorize insertion")
assert(workflow.composer("surface unavailable") == nil, "Text reads must not be treated as native composers")
assert(workflow.composer(surface) == editor, "Native OMP's dock editor must be discovered")
local dock_only = { surface = surface.surface, dock = surface.dock }
assert(workflow.composer(dock_only) == editor, "A root=dock surface read must not require main history")
assert(workflow.insertion_state(destination, second, pane, true, dock_only) == "ready")
local rooted_dock = { surface = surface.surface, main = surface.dock }
assert(workflow.composer(rooted_dock) == editor, "Selected dock roots returned under main must expose the composer")
assert(workflow.insertion_state(destination, second, pane, true, rooted_dock) == "ready")
assert(workflow.insertion_state(destination, second, pane, true, nil) == "waiting")
assert(workflow.insertion_state(destination, second, pane, true, surface) == "ready")
editor.p.sendable = false
assert(workflow.insertion_state(destination, second, pane, true, surface) == "waiting", "An unready composer must not receive insertion")
editor.p.sendable = true
editor.id = "another-agent/prompt"
assert(workflow.composer(surface) == editor, "Composer discovery must not hard-code OMP's current node id")
second.state = "reconnecting"
assert(workflow.insertion_state(destination, second, pane, true, surface) == "changed", "Disconnect while waiting must prevent insertion")
second.state = "connected"
pane.running.line = "vim"
assert(workflow.insertion_state(destination, second, pane, true, surface) == "changed", "Program changes while waiting must prevent insertion")
pane.running.line = "omp"
pane.program = "/bin/zsh"
assert(workflow.insertion_state(destination, second, pane, true, surface) == "changed", "Underlying program changes remain unsafe")
pane.program = "/bin/bash"
assert(workflow.insertion_state(destination, second, pane, false, surface) == "changed", "Agent identity must still match during readiness")
second.address = "replacement"
assert(workflow.insertion_state(destination, second, pane, true, surface) == "changed", "Reused host slots must not receive an old insertion")
second.address = "second"

local malformed = { surface = { id = "other", mode = "screen" }, main = { id = "text", k = "editor", p = { text = references } } }
assert(workflow.composer(malformed) == nil, "Ordinary text editors without sendable metadata are not agent composers")
local unready = { id = "unready", k = "editor", p = { text = "", sendable = false } }
surface.main = unready
assert(workflow.composer(surface) == editor, "An unready editor in another region must not hide a ready dock composer")
print("PASS: host selection, stale-destination safety, shell quoting, OMP references and composer readiness")
