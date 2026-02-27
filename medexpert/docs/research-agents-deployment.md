# Research Agents -- Infrastructure Requirements

The research specialist agents (orchestrator, 8 domain specialists, verifier, reviser)
are NOT included in the medliaison Cloud Run deployment.

## Why
These agents depend on MCP servers for tool execution:
- PubMed MCP server (port 9001)
- ClinicalTrials.gov MCP server (port 9002)
- OpenFDA MCP server (port 9003)
- CDC MCP server (port 9004)
- Regulatory MCP server (port 9005)
- SEER MCP server (port 9006)
- Genomic MCP server (port 9007)
- Environmental MCP server (port 9008)
- Census SDOH MCP server (port 9009)
- Provider Intel MCP server (port 9010)
- Knowledge Graph MCP server (port 9011)
- Societies MCP server (port 9012)
- VigiBase MCP server (port 9013)

Running these agents without their MCP servers causes SAM to enter a crash/restart
loop (exit 0 every ~70 seconds).

## Deployment Path for Research Agents
Research agents require either:
1. MCP servers deployed as separate Cloud Run services (see medexpert/infra/terraform/)
   and connected via VPC or public URLs before the research agents start
2. A separate Cloud Run service dedicated to research agents with MCP server URLs
   injected as environment variables

## Current Deployment Scope
The medliaison service deploys the triage pipeline only:
- TriageIntakeAgent
- TriageOrchestratorAgent
- HTTP-SSE Gateway (webui)

This is intentional. See feature/triage-pipeline PR for scope.
