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
local function has_root(roots: { { dir: string, kind: string } }, dir: string, kind: string): boolean
	for _, root in roots do
		if root.dir == dir and root.kind == kind then
			return true
		end
	end
	return false
end

local mac_roots = workflow.python_search_roots("macos", "/Users/dev", "")
assert(has_root(mac_roots, "/opt/homebrew/bin", "bin"), "Apple Silicon Homebrew bin must be searched")
assert(has_root(mac_roots, "/opt/homebrew/opt", "keg"), "Unlinked Homebrew kegs must be searchable")
assert(has_root(mac_roots, "/usr/local/bin", "bin"), "Intel Homebrew and python.org shims must be searched")
assert(has_root(mac_roots, "/Library/Frameworks/Python.framework/Versions", "framework"))
assert(has_root(mac_roots, "/Users/dev/.pyenv/versions", "version"))
assert(has_root(mac_roots, "/Users/dev/.local/share/uv/python", "version"))
assert(not has_root(mac_roots, "/usr/bin", "bin"), "Apple's /usr/bin/python3 must not be a candidate directory")
assert(not has_root(workflow.python_search_roots("macos", "", ""), "/.pyenv/versions", "version"), "An empty home must not invent a search root")
local win_roots = workflow.python_search_roots("windows", "", "C:\\Users\\dev\\AppData\\Local")
assert(has_root(win_roots, "C:\\Users\\dev\\AppData\\Local\\Programs\\Python", "windows"))

local brew_names = workflow.python_candidates("/opt/homebrew/bin", "bin", {
	"python3.13", "python3.14", "python3.14-config", "python3.14t", "pip3.14",
})
assert(#brew_names == 2 and brew_names[1] == "/opt/homebrew/bin/python3.13" and brew_names[2] == "/opt/homebrew/bin/python3.14", "Only versioned python3.N binaries are candidates")
local framework = workflow.python_candidates("/Library/Frameworks/Python.framework/Versions", "framework", { "3.13", "3.14", "Current" })
assert(#framework == 2 and framework[2] == "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3", "Current and non-version names are not interpreters")
local kegs = workflow.python_candidates("/opt/homebrew/opt", "keg", { "python@3.9", "python@3.14", "python@3.13" })
assert(#kegs == 3 and kegs[1] == "/opt/homebrew/opt/python@3.9/bin/python3.9", "An old keg is reported so the error can name it")
local managers = workflow.python_candidates("/Users/dev/.local/share/uv/python", "version", { "cpython-3.14.8-macos-aarch64-none", "notes" })
assert(managers[1] == "/Users/dev/.local/share/uv/python/cpython-3.14.8-macos-aarch64-none/bin/python3")
local win_installs = workflow.python_candidates("C:\\Users\\dev\\AppData\\Local\\Programs\\Python", "windows", { "Python39", "Python314", "Python312-32" })
assert(#win_installs == 3 and win_installs[2] == "C:\\Users\\dev\\AppData\\Local\\Programs\\Python\\Python314\\python.exe")

local kept = workflow.resolve_python({ "/opt/homebrew/bin/python3.13", "-I" }, "macos", true, {
	"/opt/homebrew/bin/python3.14",
})
assert(kept[1] == "/opt/homebrew/bin/python3.13" and kept[2] == "-I", "An existing interpreter override must be kept")
local discovered = workflow.resolve_python({ "python3" }, "macos", false, {
	"/usr/bin/python3",
	"/usr/local/bin/python3",
	"/opt/homebrew/bin/python3.13",
	"/opt/homebrew/bin/python3.14",
	"/usr/local/bin/python3.14",
	"/Library/Frameworks/Python.framework/Versions/3.14/bin/python3",
})
assert(discovered[1] == "/opt/homebrew/bin/python3.14", "macOS must use the newest Homebrew python3.N, not PATH or Apple python3")
local newer_framework = workflow.resolve_python({ "/opt/homebrew/bin/python3" }, "macos", false, {
	"/opt/homebrew/bin/python3.13",
	"/Library/Frameworks/Python.framework/Versions/3.14/bin/python3",
})
assert(newer_framework[1] == "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3", "A newer framework install beats an older Homebrew install")
local same = workflow.resolve_python({ "python3", "-I" }, "macos", false, {
	"/Library/Frameworks/Python.framework/Versions/3.14/bin/python3",
	"/opt/homebrew/bin/python3.14",
})
assert(same[1] == "/opt/homebrew/bin/python3.14" and same[2] == "-I", "The same version prefers the Homebrew prefix and keeps later argv")
local pyenv = workflow.resolve_python({ "python3" }, "macos", false, {
	"/Users/dev/.pyenv/versions/3.12.0/bin/python3",
	"/Users/dev/.local/share/uv/python/cpython-3.11.9-macos-aarch64-none/bin/python3",
})
assert(pyenv[1] == "/Users/dev/.pyenv/versions/3.12.0/bin/python3", "Version managers are used when no Homebrew or framework install exists")
local function failure(argv: { string }, candidates: { string }): string
	local ok, err = pcall(workflow.resolve_python, argv, "macos", false, candidates)
	assert(not ok, "An unusable macOS interpreter must fail before launch")
	return tostring(err)
end
local apple_only = failure({ "python3" }, { "/usr/bin/python3" })
assert(string.find(apple_only, "Found Apple's /usr/bin/python3", 1, true) ~= nil, apple_only)
assert(string.find(apple_only, "too old", 1, true) ~= nil, apple_only)
assert(string.find(apple_only, "brew install python", 1, true) ~= nil, apple_only)
local old_keg = failure({ "python3" }, { "/opt/homebrew/opt/python@3.9/bin/python3.9" })
assert(string.find(old_keg, "Python 3.9", 1, true) ~= nil, old_keg)
assert(string.find(old_keg, "/opt/homebrew/opt/python@3.9/bin/python3.9", 1, true) ~= nil, old_keg)
assert(string.find(old_keg, "brew install python", 1, true) ~= nil, old_keg)
local none = failure({ "/opt/homebrew/bin/python3" }, {})
assert(string.find(none, "not installed", 1, true) ~= nil, none)
assert(string.find(none, "/usr/bin/python3 is not present", 1, true) ~= nil, none)
assert(string.find(none, "brew install python", 1, true) ~= nil, none)
local pinned_old_ok, pinned_old_err = pcall(workflow.resolve_python, { "/usr/bin/python3" }, "macos", true, { "/opt/homebrew/bin/python3.14" })
assert(not pinned_old_ok)
assert(string.find(tostring(pinned_old_err), "too old", 1, true) ~= nil, tostring(pinned_old_err))
assert(string.find(tostring(pinned_old_err), "/opt/homebrew/bin/python3.14", 1, true) ~= nil, tostring(pinned_old_err))
assert(string.find(tostring(pinned_old_err), "Remove the python override", 1, true) ~= nil, tostring(pinned_old_err))
local linux = workflow.resolve_python({ "python3" }, "linux", false, { "/opt/homebrew/bin/python3.14" })
assert(linux[1] == "python3", "Linux keeps PATH python3")
local windows = workflow.resolve_python({ "py", "-3" }, "windows", false, {})
assert(windows[1] == "py" and windows[2] == "-3", "Windows keeps the py launcher")
local windows_missing = workflow.resolve_python({ "C:\\missing\\python.exe" }, "windows", false, {
	"C:\\Users\\dev\\AppData\\Local\\Programs\\Python\\Python312\\python.exe",
	"C:\\Users\\dev\\AppData\\Local\\Programs\\Python\\Python314\\python.exe",
})
assert(windows_missing[1] == "C:\\Users\\dev\\AppData\\Local\\Programs\\Python\\Python314\\python.exe", "A missing Windows override uses the newest installed Python")


print("PASS: host selection, stale-destination safety, shell quoting, OMP references and composer readiness")
