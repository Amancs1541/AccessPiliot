# AccessPilot — Active Directory connectivity agent

**Phase 1 of the Active Directory connector.** This agent does not talk to Active Directory yet — it only proves
this machine can reach your AccessPilot instance over HTTPS. LDAP sync (users, groups, roles, provisioning)
arrives in a later phase.

## Why an agent at all?

Entra and Okta are cloud APIs AccessPilot can call directly. On-premises Active Directory's LDAP port is normally
only reachable from inside your own network — so instead of AccessPilot reaching in, this agent runs inside your
network and calls **out** to AccessPilot. No inbound firewall rule is ever needed on your side.

## Requirements

- Python 3.8 or newer, already installed on most Windows Server / Linux boxes. No `pip install` needed — this
  script uses only the standard library.
- A machine inside your network with outbound HTTPS access to your AccessPilot instance (this doesn't need to be
  a domain controller itself — any server or workstation with network line-of-sight works for this phase).

## Setup

1. In AccessPilot, go to **Providers** → **Active Directory** → **Agent Connectivity** → **Generate agent key**.
   Copy the key shown — it is **only ever shown once**.
2. Note the Provider ID shown on the same page.
3. On the machine that will run the agent, set three environment variables:

   **Windows (PowerShell):**
   ```powershell
   $env:ACCESSPILOT_URL = "https://your-accesspilot-instance.example.com"
   $env:ACCESSPILOT_PROVIDER_ID = "<the provider id from step 2>"
   $env:ACCESSPILOT_AGENT_KEY = "<the key from step 1>"
   ```

   **Linux / macOS:**
   ```bash
   export ACCESSPILOT_URL="https://your-accesspilot-instance.example.com"
   export ACCESSPILOT_PROVIDER_ID="<the provider id from step 2>"
   export ACCESSPILOT_AGENT_KEY="<the key from step 1>"
   ```

4. Test connectivity once:
   ```bash
   python accesspilot_agent.py --once
   ```
   This should print `Heartbeat succeeded.` and exit. If it fails, the error message tells you whether it's a
   network problem (can't reach AccessPilot at all) or an authentication problem (wrong provider id / key).

5. Run it for real (stays running, heartbeats every 60 seconds):
   ```bash
   python accesspilot_agent.py
   ```
   For a real deployment, run this under a process supervisor (systemd, NSSM/Windows Service, pm2, etc.) so it
   restarts automatically — not covered by this phase, a later phase will include a proper install script.

## What AccessPilot shows

Once the agent sends its first successful heartbeat, the Active Directory provider's card shows **"Agent
connected"** with a last-seen timestamp. If no heartbeat arrives for a few minutes, it reverts to showing the
agent as disconnected — this is a live recency check, not a one-time confirmation.

## Regenerating the key

Generating a new agent key immediately invalidates the old one — any agent process still using the old key will
start failing its heartbeats until you update its `ACCESSPILOT_AGENT_KEY` to the new value.
