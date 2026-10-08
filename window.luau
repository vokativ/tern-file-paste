--!strict
local workflow = require("./workflow")
local config_path = tern.plugin.data .. "/config.json"
local NOTICE_KEY = "file_paste_notice_v1"
local busy = false
local sequence = 0
local POLL_MS = 250
local READY_MS = 10000

type Upload = {
	pane: number?,
	panel_host: number?,
	progress_file: string?,
	timer: TimerHandle?,
	active: boolean,
	phase: string,
	sent: number,
	total: number,
	files: number,
	file_index: number,
	host: string,
	status: string,
	detail: string,
	error: string?,
	paths: { string }?,
	directory: string?,
	references: string?,
}
local latest: Upload? = nil

-- Only our elements are styled; host toasts deliberately keep their native style.
tern.css("upload-status", [[
.file-paste-upload { display: flex; flex-direction: column; gap: 12px; padding: 16px; min-width: 0; }
.file-paste-upload .file-paste-line { white-space: pre-wrap; overflow-wrap: anywhere; min-width: 0; }
.file-paste-upload .file-paste-heading { font-weight: 600; }
.file-paste-upload .file-paste-copy { align-self: flex-start; }
]])

local function line(text: string, heading: boolean?): Node
	return tern.ui.el("div", { class = "file-paste-line" .. (if heading then " file-paste-heading" else ""), text = text })
end

local function upload_view(upload: Upload): Node
	local phase: { [string]: string } = {
		reading = "Reading copied files",
		connecting = "Connecting to SSH",
		uploading = "Uploading source bytes",
		finalizing = "Finalizing remote files",
		done = "Upload completed",
		failed = "Upload failed",
	}
	local ratio = if upload.total > 0 then math.clamp(upload.sent / upload.total, 0, 1)
		else (if upload.phase == "done" then 1 elseif upload.phase == "failed" then 0 else nil)
	local parts = {
		line(upload.status, true),
		line(if upload.host ~= "" then "Destination: " .. upload.host else "Remote File Paste"),
		tern.ui.progress(ratio, phase[upload.phase] or upload.phase),
		line(string.format("%.2f / %.2f MiB · %d file(s) · current file %d", upload.sent / (1024 * 1024), upload.total / (1024 * 1024), upload.files, upload.file_index)),
		line("Progress counts source bytes consumed by SSH, not confirmed remote receipt. Completion requires every SSH process to succeed."),
		line(upload.detail),
	}
	if upload.error ~= nil then
		table.insert(parts, line(upload.error))
	end
	if upload.directory ~= nil then
		table.insert(parts, line("Remote directory: " .. upload.directory))
	end
	if upload.paths ~= nil then
		table.insert(parts, line("Uploaded paths:\n" .. table.concat(upload.paths, "\n")))
	end
	if upload.references ~= nil then
		table.insert(parts, line("References:\n" .. upload.references))
		table.insert(parts, tern.ui.el("button", {
			class = "file-paste-copy", text = "Copy references", actions = { click = "copy-references" },
		}))
	end
	return tern.ui.el("div", { class = "file-paste-upload" }, parts)
end

-- Closing a canvas or a failed render must never cancel the transfer or insertion.
local function show_status(cx: WindowCx, upload: Upload, open: boolean)
	local ok, failure = pcall(function()
		if upload.pane ~= nil and cx.canvas:get(upload.pane) == nil then
			-- A remote canvas replica may not have arrived yet. Keep its handle
			-- during automatic updates so successful completion can close it.
			if not open then
				return
			end
			upload.pane = nil
		end
		if upload.pane ~= nil then
			cx.canvas:set(upload.pane, { view = upload_view(upload) })
		elseif open then
			local pane, reason = cx.canvas:open({ title = "File upload", view = upload_view(upload), how = "below", focus = false })
			if pane == nil then
				error(reason or "Native canvas is unavailable")
			end
			upload.pane = pane
		end
	end)
	if not ok then
		tern.log.warn("File upload status panel:", failure)
		if open then
			cx:toast("error", "Status panel unavailable", "Use Upload status to retry.")
		end
	end
end

local function cleanup(upload: Upload)
	local timer = upload.timer
	upload.timer = nil
	if timer ~= nil then
		local ok, failure = pcall(function()
			timer:cancel()
		end)
		if not ok then
			tern.log.warn("File upload timer cleanup:", failure)
		end
	end
	if upload.progress_file ~= nil then
		local path = upload.progress_file
		upload.progress_file = nil
		local ok, failure = pcall(function()
			if tern.fs.exists(path) then
				tern.fs.remove(path)
			end
		end)
		if not ok then
			tern.log.warn("File upload progress cleanup:", failure)
		end
	end
end

local function finish(cx: WindowCx?, upload: Upload, level: ToastLevel, title: string, status: string, detail: string)
	upload.active = false
	busy = false
	cleanup(upload)
	upload.status = status
	upload.detail = detail
	if level == "success" and latest == upload then
		latest = nil
	end
	if cx ~= nil then
		if level == "success" then
			local pane = upload.pane
			if pane ~= nil then
				local ok, failure = pcall(function()
					local canvas = cx.canvas:get(pane)
					-- Never raw-close a handle whose current ownership is unknown.
					if canvas ~= nil and canvas.owner == tern.plugin.id then
						cx.layout:close(pane)
					end
				end)
				if ok then
					upload.pane = nil
				else
					tern.log.warn("File upload panel dismissal:", failure)
				end
			end
		else
			show_status(cx, upload, false)
		end
		if level ~= "success" then
			cx:toast(level, title, "Details in Upload status.")
		end
	end
end

local function read_progress(upload: Upload): boolean
	local path = upload.progress_file
	if path == nil then
		return false
	end
	-- A snapshot may not exist yet; malformed or unavailable snapshots are not a
	-- substitute for the helper's authoritative exit status and diagnostics.
	local ok, changed = pcall(function(): boolean
		local snapshot = tern.json.decode(tern.fs.read(path, 8192))
		if type(snapshot) ~= "table" then
			return false
		end
		local updated = false
		if snapshot.phase == "reading" or snapshot.phase == "connecting" or snapshot.phase == "uploading"
			or snapshot.phase == "finalizing" or snapshot.phase == "done" or snapshot.phase == "failed" then
			updated = upload.phase ~= snapshot.phase
			upload.phase = snapshot.phase
		end
		for _, field in { "sent", "total", "files", "file_index" } do
			local value = snapshot[field]
			if type(value) == "number" and value >= 0 and value % 1 == 0 and (upload :: any)[field] ~= value then
				updated = true
				(upload :: any)[field] = value
			end
		end
		return updated
	end)
	return ok and changed == true
end

local function poll_progress(upload: Upload)
	upload.timer = tern.timer(POLL_MS, function(cx: WindowCx?)
		upload.timer = nil
		if not upload.active or upload.progress_file == nil then
			return
		end
		local changed = read_progress(upload)
		if changed and cx ~= nil then
			show_status(cx, upload, false)
		end
		poll_progress(upload)
	end)
end

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

local function destination_now(cx: WindowCx, destination: workflow.Destination): (ManagedHost?, PaneInfo?, boolean)
	return workflow.host_for_pane(cx.hosts:list(), destination.pane), cx.session:pane(destination.pane), workflow.is_agent(cx.agents:list(), destination.pane)
end

local function insert_references(cx: WindowCx, upload: Upload, destination: workflow.Destination, text: string)
	local deadline = tern.now() + READY_MS
	local function check(fresh: WindowCx?)
		if not upload.active then
			return
		end
		if fresh == nil then
			finish(nil, upload, "error", "Insertion unconfirmed", "Upload completed; insertion unconfirmed", "The destination window is unavailable. Use the retained references; no insertion will be retried.")
			return
		end
		local ok, failure = pcall(function()
			local host, pane, agent = destination_now(fresh, destination)
			local surface = if workflow.same_destination(destination, host, pane, agent)
				then fresh.session:surface(destination.pane, { as = "table", root = "dock", nodes = 500 }) else nil
			local state = workflow.insertion_state(destination, host, pane, agent, surface)
			if state == "changed" then
				finish(fresh, upload, "error", "Upload complete", "Upload completed; destination changed", "The original host, pane, program or agent changed or disconnected. No insertion is retried; copy the retained references manually.")
				return
			end
			if tern.now() >= deadline then
				finish(fresh, upload, "error", "Input not ready", "Upload completed; input not ready", "The original agent composer did not become ready within 10 seconds. Copy the retained references when it is ready; no insertion was sent.")
				return
			end
			if state == "ready" then
				fresh:run(destination.pane, text)
				finish(fresh, upload, "success", "References inserted", "Upload completed; insertion dispatched", "References were sent once to the ready original composer. No Enter key was sent.")
				return
			end
			show_status(fresh, upload, false)
			upload.timer = tern.timer(POLL_MS, function(next_cx: WindowCx?)
				upload.timer = nil
				check(next_cx)
			end)
		end)
		if not ok then
			upload.error = tostring(failure)
			finish(fresh, upload, "error", "Insertion failed", "Upload completed; insertion failed", "The insertion call failed. Retained references are available below; no automatic retry occurs.")
		end
	end
	check(cx)
end

local function upload(cx: WindowCx, record: Upload)
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
	-- An existing status canvas on another tab/host would be invisible here.
	-- New uploads may reuse it only beside this operation's original destination.
	if record.pane ~= nil and (record.panel_host ~= host.id
		or cx.session:tab_of(record.pane) ~= cx.session:tab_of(destination.pane)) then
		record.pane = nil
	end
	if record.pane == nil then
		-- Native canvases outlive this VM. Recover only our status panel on the
		-- original destination's tab and host, never another plugin's canvas.
		local ok, failure = pcall(function()
			local tab = cx.session:tab_of(destination.pane)
			local hosts = cx.hosts:list()
			for _, canvas in cx.canvas:list() do
				if canvas.owner == tern.plugin.id and canvas.title == "File upload"
					and tab ~= nil and cx.session:tab_of(canvas.pane) == tab then
					local panel_host = workflow.host_for_pane(hosts, canvas.pane)
					if panel_host ~= nil and panel_host.id == destination.host
						and panel_host.address == destination.address then
						record.pane = canvas.pane
						break
					end
				end
			end
		end)
		if not ok then
			tern.log.warn("File upload panel recovery:", failure)
		end
	end
	record.panel_host = host.id
	local output_mode = if mode == "auto" then (if agent then "omp" else "paths") else mode
	sequence += 1
	-- Independent window VMs may upload concurrently; neither shares a sidecar.
	local nonce = string.format("%.0f-%d-%d-%d-%d", tern.now() * 1000, math.random(1, 2147483647), math.random(1, 2147483647), math.random(1, 2147483647), sequence)
	tern.fs.mkdir(tern.plugin.data)
	record.progress_file = tern.plugin.data .. "/upload-progress-" .. nonce .. ".json"
	request.progress_file = record.progress_file
	record.host = host.name
	record.active = true
	busy = true
	show_status(cx, record, true)
	poll_progress(record)
	tern.process.run(argv, {
		stdin = tern.json.encode(request),
		timeout_ms = (request.timeout + 30) * 1000,
	}, function(result: ProcessResult, fresh: WindowCx?)
		local ok, failure = pcall(function()
			read_progress(record)
			cleanup(record)
			if result.status ~= 0 then
				record.phase = "failed"
				record.error = (if result.timed_out then "The upload helper timed out.\n" else "") .. result.stderr
				finish(fresh, record, "error", "Upload failed", "Upload failed; nothing pasted", "Full helper diagnostics are below.")
				return
			end
			local response = tern.json.decode(result.stdout)
			assert(type(response) == "table" and type(response.paths) == "table" and type(response.directory) == "string", "Upload returned an invalid result")
			record.paths = response.paths
			record.directory = response.directory
			record.phase = "done"
			record.files = #response.paths
			if type(response.bytes) == "number" then
				record.sent = response.bytes
				record.total = response.bytes
			end
			local text = workflow.format_paths(response.paths, output_mode)
			record.references = text
			if fresh == nil then
				finish(nil, record, "error", "Insertion unconfirmed", "Upload completed; insertion unconfirmed", "The destination window is unavailable. References were not inserted.")
				return
			end
			if destination.agent then
				record.status = "Upload completed; waiting for composer"
				record.detail = "Waiting up to 10 seconds for the original agent composer. No Enter key is sent."
				insert_references(fresh, record, destination, text)
				return
			end
			local current_host, current_pane, current_agent = destination_now(fresh, destination)
			if not workflow.same_destination(destination, current_host, current_pane, current_agent) then
				finish(fresh, record, "error", "Upload complete", "Upload completed; destination changed", "The original destination changed or disconnected. References were not inserted; copy them below.")
				return
			end
			fresh:run(destination.pane, text)
			finish(fresh, record, "success", "Upload complete", "Upload completed; references sent to terminal", "References were sent once to the original terminal; terminal insertion is not acknowledged. Review them and press Enter yourself when ready.")
		end)
		if not ok then
			record.error = tostring(failure)
			if result.status == 0 then
				finish(fresh, record, "error", "Insertion unconfirmed", "Upload completed; insertion unconfirmed", "The helper succeeded, but references could not be inserted or confirmed. Any returned paths are retained below.")
			else
				record.phase = "failed"
				finish(fresh, record, "error", "Upload failed", "Upload failed; nothing pasted", "The helper failed and completion handling encountered an error. Diagnostics are retained below.")
			end
		end
	end)
end

tern.command({
	id = "paste",
	title = "Paste local files to remote",
	icon = "files",
	keys = { if tern.runtime.os == "macos" then "cmd+alt+shift+v" else "ctrl+;" },
	run = function(cx: WindowCx)
		if busy then
			cx:toast("info", "Upload already running", "Open Upload status.")
			return
		end
		local record: Upload = {
			active = false, phase = "reading", sent = 0, total = 0, files = 0, file_index = 0,
			host = "", status = "Uploading copied files", detail = "Copied files are transferred through SSH. Your prompt will not be submitted.",
			pane = if latest ~= nil then latest.pane else nil,
			panel_host = if latest ~= nil then latest.panel_host else nil,
		}
		local focused = cx.session:focused()
		if record.pane ~= nil and (focused == nil
			or cx.session:tab_of(record.pane) ~= cx.session:tab_of(focused)) then
			record.pane = nil
			record.panel_host = nil
		end
		latest = record
		local ok, failure = pcall(upload, cx, record)
		if not ok then
			record.phase = "failed"
			record.error = tostring(failure)
			finish(cx, record, "error", "Upload not started", "Files were not pasted", "Resolve the destination or configuration error below, then invoke Paste local files to remote explicitly.")
			show_status(cx, record, true)
		end
	end,
})

tern.command({
	id = "status",
	title = "Remote File Paste: Upload status",
	icon = "files",
	available = function(_cx: WindowCx)
		return latest ~= nil
	end,
	run = function(cx: WindowCx)
		if latest == nil then
			return
		end
		show_status(cx, latest, true)
	end,
})

tern.on("canvas_action", function(ev: CanvasAction, cx: WindowCx): boolean?
	local record = latest
	if record == nil or ev.pane ~= record.pane then
		return nil
	end
	if ev.act == "copy-references" and record.references ~= nil then
		cx:copy(record.references)
		cx:toast("success", "References copied")
		return true
	end
	return nil
end)

tern.command({
	id = "configure",
	title = "Remote File Paste: Open keyboard settings",
	icon = "settings",
	run = function(cx: WindowCx)
		cx:command("preferences")
		cx:toast("info", "Select 07 · Keyboard", "Search for file-paste.")
	end,
})

tern.on("window_start", function(cx: WindowCx)
	if tern.kv.get(NOTICE_KEY) then
		return
	end
	tern.kv.set(NOTICE_KEY, true)
	cx:toast("info", if tern.runtime.os == "macos" then "Paste files: ⌘⌥⇧V" else "Paste files: Ctrl + ;", "Rebind in Preferences → Keyboard.")
end)
