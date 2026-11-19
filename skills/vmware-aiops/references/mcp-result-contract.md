# Shared result contract (unreleased migration)

Requires Policy 1.26.0. Failed MCP calls expose a string error and nullable hint
in `structuredContent.vmware_error`; authored content and structured business
fields remain. Success values, tool parameters/defaults and all existing
Policy/RBAC, audit and confirmation behavior are unchanged.

For tools accepting offset, pass next_offset back as offset until null.
Legacy field names, flag semantics, hint values and optional field presence
remain unchanged in this stage. No-offset tools and cursor APIs retain their
own retrieval mechanisms. Existing has_more/truncated fields are not silently
redefined, and no duplicate row aliases are introduced.

Publish Policy first, then refresh this repository's lock and test the packaged
dependency before merging/publishing. Source-overlay tests are not a release.
