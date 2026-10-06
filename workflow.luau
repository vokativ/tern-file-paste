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

return workflow
