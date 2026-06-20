# deployagent — Claude Instructions

## MCP Tools (ALWAYS use these, never run scripts directly)

This project has a registered MCP server: `deployagent-validator`.
**For ALL deployment and validation tasks, use the MCP tools below. Never use Bash, shell commands, or python scripts directly.**

### Available tools and when to use them

| Task | Tool to use |
|---|---|
| Before ANY deploy — review config + check AWS | `mcp__deployagent-validator__prepare_deploy` |
| Update deploy.yaml fields with user answers | `mcp__deployagent-validator__update_deploy_config` |
| Deploy a project | `mcp__deployagent-validator__deploy` |
| Check deployment progress | `mcp__deployagent-validator__get_deploy_logs` |
| View live app logs (CloudWatch) | `mcp__deployagent-validator__get_service_logs` |
| Deployment failed / health check failing | `mcp__deployagent-validator__get_ecs_diagnostics` |
| Validate Dockerfile | `mcp__deployagent-validator__validate_dockerfile` |
| Validate K8s/YAML file | `mcp__deployagent-validator__validate_k8s_manifest` |
| Check all files before deploy | `mcp__deployagent-validator__pre_deploy_check` |
| Apply a fix to a file | `mcp__deployagent-validator__apply_fix` |

### Examples

- User says "deploy the project" → FIRST call `prepare_deploy`, present the current config to the user, ask the questions listed in the output, collect their answers, call `update_deploy_config` with any changes, THEN call `deploy` and poll `get_deploy_logs` every 10-15 seconds
- User says "what's the deployment status?" → call `get_deploy_logs` with the same deploy.yaml path
- User says "show me logs" or "view app logs" or "tail logs" → call `get_service_logs` with the deploy.yaml path
- User says "deployment failed" or "health check failing" or "task keeps crashing" → call `get_ecs_diagnostics` to read CloudWatch logs + ECS events + stopped task reasons, then diagnose and fix automatically
- User says "check my Dockerfile" → call `validate_dockerfile` with the path
- User says "deploy `d:\Programing\Projects\MyApp`" → call `prepare_deploy` first, then follow the flow above

## Rules

1. **Never** run `deployagent apply ...` via Bash
2. **Never** run `python` or shell commands to trigger a deployment
3. Always use the MCP `deploy` tool — it handles validation + deployment internally
4. **Always** call `prepare_deploy` before `deploy` — ask the user to confirm container name, port, tag, and count before starting
5. If a resource already exists in AWS (shown in `prepare_deploy` output), warn the user before proceeding
6. If the MCP server is not connected (`/mcp` shows no `deployagent-validator`), tell the user to reload the VSCode window before proceeding
