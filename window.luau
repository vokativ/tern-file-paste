--!strict
local workflow = require("./workflow")
local config_path = tern.plugin.data .. "/config.json"
local NOTICE_KEY = "file_paste_notice_v1"
local busy = false

local function remote_destination(cx: WindowCx): (ManagedHost?, PaneInfo?)
	local id = cx.session:focused()
	if id == nil then
		return nil, nil
	end
	local host = workflow.host_for_pane(cx.hosts:list(), id)
	local pane = cx.session:pane(id)
	if host == nil or host.kind ~= "remote" or host.state ~= "connected" or pane == nil or pane.exited ~= nil then
		return nil, nil
	end
	if pane.kind ~= "terminal" and pane.kind ~= "agent" then
		return nil, nil
	end
	return host, pane
end

local function configuration(host: ManagedHost): (any, { string }, string)
	assert(tern.fs.exists(config_path), "Configure this plugin at " .. config_path .. " using config.example.json before pasting")
	local config = tern.json.decode(tern.fs.read(config_path, 65536))
	assert(type(config) == "table" and type(config.hosts) == "table", "Configuration needs a hosts object")
	local entry = config.hosts[host.address or ""] or config.hosts[host.name]
	assert(type(entry) == "table", "No SSH mapping for Tern host " .. (host.address or host.name) .. " in " .. config_path)
	assert(type(entry.target) == "string" and entry.target ~= "", "Host mapping needs an SSH target")
	local timeout = entry.timeout or config.timeout or 300
	assert(type(timeout) == "number" and timeout >= 1 and timeout % 1 == 0, "timeout must be a positive number of seconds")
	local mode = entry.format or "auto"
	assert(mode == "auto" or mode == "paths" or mode == "omp", "format must be auto, paths, or omp")
	local python = config.python or (if tern.runtime.os == "windows" then { "py", "-3" } else { "python3" })
	assert(type(python) == "table" and #python > 0, "python must be a nonempty argv array")
	local argv: { string } = {}
	for _, argument in python do
		assert(type(argument) == "string" and argument ~= "", "python argv entries must be nonempty strings")
		table.insert(argv, argument)
	end
	table.insert(argv, tern.plugin.dir .. "/helpers/paste_files.py")
	return {
		target = entry.target,
		port = entry.port,
		identity_file = entry.identity_file,
		timeout = timeout,
	}, argv, mode
end

local function upload(cx: WindowCx)
	if busy then
		cx:toast("info", "A file upload is already running")
		return
	end
	local host, pane = remote_destination(cx)
	assert(host ~= nil and pane ~= nil, "Focus a connected remote terminal or OMP pane first")
	local request, argv, mode = configuration(host)
	local agent = workflow.is_agent(cx.agents:list(), pane.pane)
	local destination: workflow.Destination = {
		pane = pane.pane,
		host = host.id,
		address = host.address,
		program = pane.program,
		running = if pane.running then pane.running.line else nil,
		agent = agent,
	}
	local output_mode = if mode == "auto" then (if agent then "omp" else "paths") else mode
	busy = true
	cx:toast("info", "Uploading copied files", "To " .. host.name .. "; your prompt will not be submitted")
	local started, failure = pcall(function()
		tern.process.run(argv, {
			stdin = tern.json.encode(request),
			timeout_ms = (request.timeout + 30) * 1000,
		}, function(result: ProcessResult, callback_cx: WindowCx?)
			busy = false
			if callback_cx == nil then
				return
			end
			local fresh: WindowCx = callback_cx
			if result.status ~= 0 then
				fresh:toast("error", "Files were not pasted", if result.timed_out then "The upload helper timed out" else result.stderr)
				return
			end
			local applied, detail = pcall(function()
				local response = tern.json.decode(result.stdout)
				assert(type(response) == "table" and type(response.paths) == "table" and type(response.directory) == "string", "Upload returned an invalid result")
				local text = workflow.format_paths(response.paths, output_mode)
				local current_host = workflow.host_for_pane(fresh.hosts:list(), destination.pane)
				local current_pane = fresh.session:pane(destination.pane)
				local current_agent = workflow.is_agent(fresh.agents:list(), destination.pane)
				if not workflow.same_destination(destination, current_host, current_pane, current_agent) then
					fresh:toast("error", "Files uploaded, but not pasted", "The original destination changed or disconnected. Files are at " .. response.directory)
					return
				end
				fresh:run(destination.pane, text)
				local size_mb = if response.bytes and type(response.bytes) == "number" and response.bytes > 0
					then string.format(" (%.1f MB)", response.bytes / (1024 * 1024))
					else ""
				fresh:toast("success", "Pasted " .. tostring(#response.paths) .. " remote file reference(s)" .. size_mb, "Uploaded to " .. host.name .. "; press Enter yourself when ready")
			end)
			if not applied then
				fresh:toast("error", "Files uploaded, but not pasted", tostring(detail))
			end
		end)
	end)
	if not started then
		busy = false
		error(failure)
	end
end

tern.command({
	id = "paste",
	title = "Paste local files to remote",
	icon = "files",
	keys = { if tern.runtime.os == "macos" then "cmd+alt+shift+v" else "ctrl+shift+f8" },
	run = function(cx: WindowCx)
		local ok, failure = pcall(upload, cx)
		if not ok then
			cx:toast("error", "Files were not pasted", tostring(failure))
		end
	end,
})

tern.command({
	id = "configure",
	title = "Remote File Paste: Open keyboard settings",
	icon = "settings",
	run = function(cx: WindowCx)
		cx:command("preferences")
		cx:toast("info", "Select 07 · Keyboard", "Search for 'file-paste' to customize or remove shortcuts")
	end,
})

tern.on("window_start", function(cx: WindowCx)
	if tern.kv.get(NOTICE_KEY) then
		return
	end
	tern.kv.set(NOTICE_KEY, true)
	local chord = if tern.runtime.os == "macos" then "Command + Option + Shift + V" else "Ctrl + Shift + F8"
	cx:toast(
		"info",
		"Remote File Paste ready",
		"Default shortcut: " .. chord .. " · Rebind in Preferences → Keyboard or run via Command Palette"
	)
end)
