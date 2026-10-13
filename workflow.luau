--!strict
-- Pure routing/formatting logic; no clipboard or window effects.

export type Destination = {
	pane: number,
	host: number,
	address: string?,
	program: string?,
	running: string?,
	agent: boolean,
}

local workflow = {}

function workflow.host_for_pane(hosts: { ManagedHost }, pane: number): ManagedHost?
	for _, host in hosts do
		for _, member in host.panes do
			if member == pane then
				return host
			end
		end
	end
	return nil
end

function workflow.is_agent(agents: { AgentBlock }, id: number): boolean
	for _, agent in agents do
		if agent.pane == id then
			return true
		end
	end
	return false
end

function workflow.same_destination(destination: Destination, host: ManagedHost?, pane: PaneInfo?, agent: boolean): boolean
	return host ~= nil
		and host.kind == "remote"
		and host.state == "connected"
		and host.id == destination.host
		and host.address == destination.address
		and pane ~= nil
		and pane.pane == destination.pane
		and pane.program == destination.program
		and (if pane.running then pane.running.line else nil) == destination.running
		and pane.exited == nil
		and agent == destination.agent
end

-- Native agent surfaces can be absent while their composer is bootstrapping.
-- Do not depend on an OMP-specific node id: all sendable editors use this shape.
function workflow.composer(surface: SurfaceTree | string | nil): TspNode?
	if type(surface) ~= "table" then
		return nil
	end
	local fallback: TspNode? = nil
	local function visit(node: TspNode?): TspNode?
		if node == nil then
			return nil
		end
		local props = node.p
		if node.k == "editor" and props ~= nil and type(props.sendable) == "boolean" and type(props.text) == "string" then
			if props.sendable then
				return node
			end
			fallback = fallback or node
		end
		for _, child in node.c or {} do
			local found = visit(child)
			if found ~= nil then
				return found
			end
		end
		return nil
	end
	return visit(surface.main) or visit(surface.dock) or visit(surface.layer) or fallback
end


-- A changed destination always wins; only a ready original composer permits insertion.
function workflow.insertion_state(
	destination: Destination,
	host: ManagedHost?,
	pane: PaneInfo?,
	agent: boolean,
	surface: SurfaceTree | string | nil
): string
	if not workflow.same_destination(destination, host, pane, agent) then
		return "changed"
	end
	local composer = workflow.composer(surface)
	return if composer ~= nil and composer.p ~= nil and composer.p.sendable == true then "ready" else "waiting"
end

function workflow.format_paths(paths: { string }, mode: string): string
	local formatted = {}
	for _, path in paths do
		assert(string.sub(path, 1, 1) == "/", "Upload did not return an absolute remote path")
		assert(not string.find(path, "[%c]"), "Upload returned a control character in a path")
		if mode == "omp" then
			assert(not string.find(path, '"', 1, true), "Upload returned a path OMP cannot quote")
			table.insert(formatted, '@"' .. path .. '"')
		else
			table.insert(formatted, "'" .. string.gsub(path, "'", "'\\''") .. "'")
		end
	end
	assert(#formatted > 0, "Upload returned no file paths")
	return table.concat(formatted, " ") .. " "
end

-- Dock-launched Tern does not inherit a shell PATH, and Homebrew's versioned
-- formulae (python@3.14) do not install an unversioned python3. Search the
-- install layouts developers actually have and pick the newest Python 3.10+.
type PythonRoot = { dir: string, kind: string }

function workflow.python_search_roots(os_name: string, home: string, local_app_data: string): { PythonRoot }
	local roots: { PythonRoot } = {}
	local function add(dir: string, kind: string)
		table.insert(roots, { dir = dir, kind = kind })
	end
	if os_name == "windows" then
		if local_app_data ~= "" then
			add(local_app_data .. "\\Programs\\Python", "windows")
		end
		return roots
	end
	-- Apple Silicon Homebrew, Intel Homebrew / python.org shims, Linuxbrew, MacPorts.
	for _, prefix in { "/opt/homebrew", "/usr/local", "/home/linuxbrew/.linuxbrew" } do
		add(prefix .. "/bin", "bin")
		add(prefix .. "/opt", "keg")
	end
	add("/opt/local/bin", "bin")
	if os_name == "linux" then
		add("/usr/bin", "bin")
	end
	if os_name == "macos" then
		add("/Library/Frameworks/Python.framework/Versions", "framework")
	end
	if home ~= "" then
		add(home .. "/.pyenv/versions", "version")
		add(home .. "/.asdf/installs/python", "version")
		add(home .. "/.local/share/uv/python", "version")
	end
	return roots
end

function workflow.python_candidates(dir: string, kind: string, names: { string }): { string }
	local windows = string.find(dir, "\\", 1, true) ~= nil and string.find(dir, "/", 1, true) == nil
	local separator = if windows then "\\" else "/"
	local found: { string } = {}
	local function add(path: string)
		table.insert(found, path)
	end
	if kind == "bin" then
		for _, name in names do
			if string.match(name, "^python3%.%d+$") or string.match(name, "^python3%.%d+%.exe$") then
				add(dir .. separator .. name)
			end
		end
	elseif kind == "framework" then
		for _, name in names do
			if string.match(name, "^3%.%d+$") then
				add(dir .. "/" .. name .. "/bin/python3")
			end
		end
	elseif kind == "keg" then
		for _, name in names do
			local minor = string.match(name, "^python@3%.(%d+)$")
			if minor ~= nil then
				add(dir .. "/" .. name .. "/bin/python3." .. minor)
			end
		end
	elseif kind == "version" then
		for _, name in names do
			if string.match(name, "^3%.%d+") or string.match(name, "^cpython%-3%.%d+") then
				add(dir .. "/" .. name .. "/bin/python3")
			end
		end
	elseif kind == "windows" then
		for _, name in names do
			local minor = string.match(name, "^Python3(%d+)$") or string.match(name, "^Python3(%d+)%-32$")
			if minor ~= nil then
				add(dir .. "\\" .. name .. "\\python.exe")
			end
		end
	end
	return found
end

function workflow.python_version(path: string): number?
	local minor = string.match(path, "python3%.(%d+)%.exe$") or string.match(path, "python3%.(%d+)$")
	if minor == nil then
		minor = string.match(path, "/Versions/3%.(%d+)/")
	end
	if minor == nil then
		minor = string.match(path, "python@3%.(%d+)")
	end
	if minor == nil then
		minor = string.match(path, "cpython%-3%.(%d+)")
	end
	if minor == nil then
		minor = string.match(path, "/versions/3%.(%d+)")
	end
	if minor == nil then
		minor = string.match(path, "/python/3%.(%d+)")
	end
	if minor == nil then
		minor = string.match(path, "Python3(%d+)\\") or string.match(path, "Python3(%d+)/")
	end
	local value = if minor ~= nil then tonumber(minor) else nil
	if value == nil or value > 30 then
		return nil
	end
	return value
end

function workflow.macos_python_error(configured: string, candidates: { string }): string
	local old_path: string? = nil
	local old_minor = -1
	local apple = configured == "/usr/bin/python3"
	local usable: string? = nil
	local usable_minor = 0
	local function consider(path: string)
		if path == "/usr/bin/python3" then
			apple = true
		end
		local minor = workflow.python_version(path)
		if minor == nil then
			return
		end
		if minor < 10 then
			if minor > old_minor then
				old_path = path
				old_minor = minor
			end
		elseif minor > usable_minor then
			usable = path
			usable_minor = minor
		end
	end
	consider(configured)
	for _, path in candidates do
		consider(path)
	end
	local configured_minor = workflow.python_version(configured)
	local configured_old = configured == "/usr/bin/python3" or (configured_minor ~= nil and configured_minor < 10)
	if usable ~= nil and configured_old and configured ~= usable then
		local why = if configured == "/usr/bin/python3"
			then "Configured interpreter /usr/bin/python3 is Apple's Command Line Tools Python, which is too old."
			else string.format("Configured interpreter %s is Python 3.%d, which is too old.", configured, configured_minor)
		return why .. string.format(" This plugin needs Python 3.10 or newer. A usable interpreter is already at %s. Remove the python override to use it.", usable)
	end
	if old_path ~= nil then
		return string.format("Python is installed, but it is too old. Found %s (Python 3.%d). This plugin needs Python 3.10 or newer and will not use it. Install a current Python, then paste again: brew install python", old_path, old_minor)
	end
	if apple then
		return "Python is installed, but it is too old. Found Apple's /usr/bin/python3, which ships with the Command Line Tools and is not Python 3.10 or newer. This plugin cannot use it. Install a current Python, then paste again: brew install python"
	end
	return "Python 3.10 or newer is not installed. Checked Homebrew, the python.org framework, MacPorts, pyenv, asdf and uv, and Apple's /usr/bin/python3 is not present. Install Python, then paste again: brew install python"
end

local function too_old_python(path: string, os_name: string): boolean
	if os_name == "macos" and path == "/usr/bin/python3" then
		return true
	end
	local minor = workflow.python_version(path)
	return minor ~= nil and minor < 10
end

local function absolute_program(path: string): boolean
	return string.sub(path, 1, 1) == "/" or string.match(path, "^%a:[/\\]") ~= nil
end

local function python_preference(path: string): number
	if string.match(path, "^/opt/homebrew/bin/python3%.%d+$") then
		return 50
	end
	if string.match(path, "^/usr/local/bin/python3%.%d+$") or string.match(path, "^/home/linuxbrew/%.linuxbrew/bin/python3%.%d+$") then
		return 40
	end
	if string.match(path, "^/opt/local/bin/python3%.%d+$") then
		return 30
	end
	if string.find(path, "/Versions/", 1, true) ~= nil then
		return 20
	end
	return 10
end

function workflow.resolve_python(argv: { string }, os_name: string, requested_exists: boolean, candidates: { string }): { string }
	local program = argv[1]
	assert(type(program) == "string" and program ~= "", "python argv needs an executable")
	if absolute_program(program) and requested_exists and not too_old_python(program, os_name) then
		return argv
	end
	-- Linux python3 and Windows py are on the default GUI PATH. macOS python3 is not.
	if not absolute_program(program) and os_name ~= "macos" and not too_old_python(program, os_name) then
		return argv
	end
	local best: string? = nil
	local best_minor = 0
	local best_rank = -1
	for _, path in candidates do
		local minor = workflow.python_version(path)
		if minor ~= nil and minor >= 10 then
			local rank = python_preference(path)
			if minor > best_minor or (minor == best_minor and rank > best_rank) or (minor == best_minor and rank == best_rank and (best == nil or path < best)) then
				best = path
				best_minor = minor
				best_rank = rank
			end
		end
	end
	if best == nil or too_old_python(program, os_name) then
		if os_name == "macos" then
			error(workflow.macos_python_error(program, candidates))
		end
		error("Python 3.10+ was not found. Install it or set \"python\" to an existing 3.10+ executable.")
	end
	if best == program then
		return argv
	end
	local resolved: { string } = { best }
	for index = 2, #argv do
		resolved[index] = argv[index]
	end
	return resolved
end


return workflow
