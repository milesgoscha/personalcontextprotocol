"""
PCP Command Line Interface.

Commands:
- server start: Start the PCP node server
- agent run: Run the reference RLM agent
- token create: Create an access token
- status: Show node status
"""

import asyncio
from datetime import timedelta
from pathlib import Path

import click


@click.group()
@click.option("--data-dir", type=click.Path(), default=None, help="Data directory")
@click.pass_context
def main(ctx, data_dir):
    """PCP - Personal Context Protocol CLI."""
    ctx.ensure_object(dict)
    ctx.obj["data_dir"] = Path(data_dir) if data_dir else Path.home() / ".pcp" / "data"


# Server commands

@main.group()
def server():
    """Manage the PCP node server."""
    pass


@server.command("start")
@click.option("--host", default="127.0.0.1", help="Host to bind to")
@click.option("--port", default=6001, help="Port to bind to")
@click.pass_context
def server_start(ctx, host, port):
    """Start the PCP node server."""
    import uvicorn

    from pcp.server.app import create_app

    data_dir = ctx.obj["data_dir"]
    click.echo(f"Starting PCP node on {host}:{port}")
    click.echo(f"Data directory: {data_dir}")

    app = create_app(data_dir)
    uvicorn.run(app, host=host, port=port)


# Agent commands

@main.group()
def agent():
    """Run PCP agents."""
    pass


@agent.command("run")
@click.argument("prompt", required=False, default="What did I work on today?")
@click.option("--save/--no-save", default=False, help="Save reflection to PCP")
@click.option("--url", default="http://localhost:6001", help="PCP node URL")
@click.pass_context
def agent_run(ctx, prompt, save, url):
    """Run the reference RLM agent."""
    from pcp.agents.rlm_agent import PCPClient, RLMAgent
    from pcp.auth.tokens import create_token

    click.echo(f"Running RLM agent with prompt: {prompt}")
    click.echo(f"Connecting to: {url}")

    # Create a token for the agent
    token_string, _ = create_token(
        subject="cli-agent",
        scopes=[
            "query:event.*",
            "query:learning.*",
            "query:reflection.*",
            "reflect:write",
        ],
        expires_in=timedelta(hours=1),
    )

    client = PCPClient(base_url=url, token=token_string)
    agent = RLMAgent(client=client)

    async def run():
        try:
            result = await agent.run(prompt=prompt, save_reflection=save)
            return result
        finally:
            await client.close()

    result = asyncio.run(run())

    click.echo("\n=== Reflection ===")
    click.echo(result["reflection"]["content"])
    click.echo("\n=== Stats ===")
    for key, value in result["stats"].items():
        click.echo(f"  {key}: {value}")

    if client._mock_mode:
        click.echo("\n[Note: Server was unavailable, results are mocked]")


# Token commands

@main.group()
def token():
    """Manage access tokens."""
    pass


@token.command("create")
@click.argument("subject")
@click.option("--scope", "-s", multiple=True, help="Scopes to grant")
@click.option("--hours", default=24, help="Token lifetime in hours")
def token_create(subject, scope, hours):
    """Create a new access token."""
    from pcp.auth.tokens import create_token

    scopes = list(scope) if scope else ["query:event.summary", "query:learning.*"]

    token_string, token_obj = create_token(
        subject=subject,
        scopes=scopes,
        expires_in=timedelta(hours=hours),
    )

    click.echo(f"Token created for: {subject}")
    click.echo(f"Scopes: {', '.join(scopes)}")
    click.echo(f"Expires in: {hours} hours")
    click.echo(f"\nToken: {token_string}")


@token.command("list")
def token_list():
    """List active tokens."""
    from pcp.auth.tokens import list_tokens

    tokens = list_tokens()

    if not tokens:
        click.echo("No active tokens")
        return

    click.echo(f"Active tokens: {len(tokens)}")
    for t in tokens:
        click.echo(f"  {t.token_id}: {t.subject} (expires: {t.expires_at})")


# Collector commands

@main.group()
def collect():
    """Collect activity events."""
    pass


@collect.command("snapshot")
@click.option("--url", default="http://localhost:6001", help="PCP node URL")
def collect_snapshot(url):
    """Capture current activity and emit as event."""
    import httpx

    from pcp.collectors.activity import ActivityCollector

    collector = ActivityCollector()
    app, window = collector.get_active_window()

    if app == "Unknown":
        click.echo("Could not detect active window (grant accessibility permissions)")
        return

    # Create event
    event = collector._create_navigation_event(app=app, window_title=window)

    click.echo(f"Captured: {app} - {window[:50]}")

    # Get token and emit
    try:
        with httpx.Client(base_url=url, timeout=10.0) as client:
            # Create token
            resp = client.post("/api/token", json={
                "subject": "collector-cli",
                "scopes": ["observe:event"],
                "hours": 1,
            })
            resp.raise_for_status()
            token = resp.json()["token"]

            # Emit event
            resp = client.post(
                "/api/observe",
                json={"objects": [event]},
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            result = resp.json()

            click.echo(f"Emitted event: {result['ids'][0]}")

    except httpx.ConnectError:
        click.echo(f"Could not connect to {url} - is the server running?")
    except Exception as e:
        click.echo(f"Error: {e}")


@collect.command("watch")
@click.option("--duration", "-d", default=60, help="Duration in seconds")
@click.option("--interval", "-i", default=1.0, help="Check interval in seconds")
@click.option("--url", default="http://localhost:6001", help="PCP node URL")
def collect_watch(duration, interval, url):
    """Watch for activity changes and emit events."""
    import time

    import httpx

    from pcp.collectors.activity import ActivityCollector

    collector = ActivityCollector()

    click.echo(f"Watching for activity changes for {duration}s...")
    click.echo(f"Connecting to: {url}")
    click.echo("Switch between apps to generate events.\n")

    # Get token
    try:
        with httpx.Client(base_url=url, timeout=10.0) as client:
            resp = client.post("/api/token", json={
                "subject": "collector-cli",
                "scopes": ["observe:event"],
                "hours": 1,
            })
            resp.raise_for_status()
            token = resp.json()["token"]
            headers = {"Authorization": f"Bearer {token}"}

            start_time = time.time()
            event_count = 0

            while (time.time() - start_time) < duration:
                events = collector.check_for_events()

                for event in events:
                    summary = event.get("payload", {}).get("summary", "")
                    click.echo(f"[{time.strftime('%H:%M:%S')}] {summary}")

                    # Emit event
                    resp = client.post(
                        "/api/observe",
                        json={"objects": [event]},
                        headers=headers,
                    )
                    if resp.status_code == 200:
                        event_count += 1

                time.sleep(interval)

            click.echo(f"\nDone. Emitted {event_count} events.")

    except httpx.ConnectError:
        click.echo(f"Could not connect to {url} - is the server running?")
    except KeyboardInterrupt:
        click.echo("\nStopped.")
    except Exception as e:
        click.echo(f"Error: {e}")


# Grant management commands

@main.group()
def grants():
    """Manage access grants."""
    pass


@grants.command("list")
@click.option("--status", "-s", type=click.Choice(["pending", "approved", "denied", "revoked"]), help="Filter by status")
@click.option("--url", default="http://localhost:6001", help="PCP node URL")
def grants_list(status, url):
    """List access grants."""
    import httpx

    try:
        with httpx.Client(base_url=url, timeout=10.0) as client:
            # Get admin token
            resp = client.post("/api/token", json={
                "subject": "cli-admin",
                "scopes": ["pcp:admin"],
                "hours": 1,
            })
            resp.raise_for_status()
            token = resp.json()["token"]

            # List grants
            params = {"status": status} if status else {}
            resp = client.get(
                "/api/grants",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            result = resp.json()

            if not result["grants"]:
                click.echo("No grants found")
                return

            click.echo(f"Grants ({result['count']}):")
            for g in result["grants"]:
                status_icon = {
                    "pending": "⏳",
                    "approved": "✅",
                    "denied": "❌",
                    "revoked": "🚫",
                    "expired": "⌛",
                }.get(g["status"], "?")
                click.echo(f"  {status_icon} {g['grant_id']}: {g['client_name']} ({g['status']})")
                click.echo(f"      Scopes: {', '.join(g['scopes_requested'])}")
                click.echo(f"      Reason: {g['reason']}")

    except httpx.ConnectError:
        click.echo(f"Could not connect to {url} - is the server running?")
    except Exception as e:
        click.echo(f"Error: {e}")


@grants.command("approve")
@click.argument("grant_id")
@click.option("--scopes", "-s", multiple=True, help="Override scopes (can specify multiple)")
@click.option("--hours", "-h", type=int, help="Token lifetime in hours")
@click.option("--url", default="http://localhost:6001", help="PCP node URL")
def grants_approve(grant_id, scopes, hours, url):
    """Approve a pending grant."""
    import httpx

    try:
        with httpx.Client(base_url=url, timeout=10.0) as client:
            # Get admin token
            resp = client.post("/api/token", json={
                "subject": "cli-admin",
                "scopes": ["pcp:admin"],
                "hours": 1,
            })
            resp.raise_for_status()
            token = resp.json()["token"]

            # Approve grant
            payload = {}
            if scopes:
                payload["scopes"] = list(scopes)
            if hours:
                payload["lifetime_hours"] = hours

            resp = client.post(
                f"/api/grants/{grant_id}/approve",
                json=payload if payload else None,
                headers={"Authorization": f"Bearer {token}"},
            )

            if resp.status_code == 400:
                click.echo(f"Error: {resp.json().get('detail', 'Unknown error')}")
                return

            resp.raise_for_status()
            result = resp.json()

            click.echo(f"✅ Grant approved: {result['grant_id']}")
            click.echo(f"   Scopes: {', '.join(result['scopes_approved'])}")
            click.echo(f"   Expires: {result['expires_at']}")

    except httpx.ConnectError:
        click.echo(f"Could not connect to {url} - is the server running?")
    except Exception as e:
        click.echo(f"Error: {e}")


@grants.command("deny")
@click.argument("grant_id")
@click.option("--reason", "-r", help="Reason for denial")
@click.option("--url", default="http://localhost:6001", help="PCP node URL")
def grants_deny(grant_id, reason, url):
    """Deny a pending grant."""
    import httpx

    try:
        with httpx.Client(base_url=url, timeout=10.0) as client:
            # Get admin token
            resp = client.post("/api/token", json={
                "subject": "cli-admin",
                "scopes": ["pcp:admin"],
                "hours": 1,
            })
            resp.raise_for_status()
            token = resp.json()["token"]

            # Deny grant
            payload = {"reason": reason} if reason else {}
            resp = client.post(
                f"/api/grants/{grant_id}/deny",
                json=payload if payload else None,
                headers={"Authorization": f"Bearer {token}"},
            )

            if resp.status_code == 400:
                click.echo(f"Error: {resp.json().get('detail', 'Unknown error')}")
                return

            resp.raise_for_status()
            result = resp.json()

            click.echo(f"❌ Grant denied: {result['grant_id']}")
            if result.get("denial_reason"):
                click.echo(f"   Reason: {result['denial_reason']}")

    except httpx.ConnectError:
        click.echo(f"Could not connect to {url} - is the server running?")
    except Exception as e:
        click.echo(f"Error: {e}")


@grants.command("revoke")
@click.argument("grant_id")
@click.option("--url", default="http://localhost:6001", help="PCP node URL")
def grants_revoke(grant_id, url):
    """Revoke an approved grant."""
    import httpx

    try:
        with httpx.Client(base_url=url, timeout=10.0) as client:
            # Get admin token
            resp = client.post("/api/token", json={
                "subject": "cli-admin",
                "scopes": ["pcp:admin"],
                "hours": 1,
            })
            resp.raise_for_status()
            token = resp.json()["token"]

            # Revoke grant
            resp = client.post(
                f"/api/grants/{grant_id}/revoke",
                headers={"Authorization": f"Bearer {token}"},
            )

            if resp.status_code == 400:
                click.echo(f"Error: {resp.json().get('detail', 'Unknown error')}")
                return

            resp.raise_for_status()
            result = resp.json()

            click.echo(f"🚫 Grant revoked: {result['grant_id']}")

    except httpx.ConnectError:
        click.echo(f"Could not connect to {url} - is the server running?")
    except Exception as e:
        click.echo(f"Error: {e}")


# Audit commands

@main.group()
def audit():
    """View audit logs."""
    pass


@audit.command("list")
@click.option("--operation", "-o", help="Filter by operation type (query, observe, learn, reflect)")
@click.option("--requester", "-r", help="Filter by requester")
@click.option("--since", help="Show events after (ISO datetime)")
@click.option("--limit", "-l", default=20, help="Max events to show")
@click.option("--url", default="http://localhost:6001", help="PCP node URL")
def audit_list(operation, requester, since, limit, url):
    """List recent audit events."""
    import httpx

    try:
        with httpx.Client(base_url=url, timeout=10.0) as client:
            # Get admin token
            resp = client.post("/api/token", json={
                "subject": "cli-admin",
                "scopes": ["pcp:admin"],
                "hours": 1,
            })
            resp.raise_for_status()
            token = resp.json()["token"]

            # Query audit
            params = {"limit": limit}
            if operation:
                params["operation"] = operation
            if requester:
                params["requester"] = requester
            if since:
                params["since"] = since

            resp = client.get(
                "/api/audit",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            result = resp.json()

            if not result["events"]:
                click.echo("No audit events found")
                return

            click.echo(f"Audit events ({result['count']} of {result['total']}):")
            for event in result["events"]:
                payload = event.get("payload", {})
                detail = payload.get("detail", {})
                ts = payload.get("timestamp", "")[:19]
                op = detail.get("operation", "?")
                req = detail.get("requester", "?")
                success = "ok" if detail.get("success") else "FAIL"
                count = detail.get("result_count")
                count_str = f" ({count} results)" if count is not None else ""

                click.echo(f"  [{ts}] {op} by {req} - {success}{count_str}")

            if result["has_more"]:
                click.echo(f"\n  ... and more (use --limit to see more)")

    except httpx.ConnectError:
        click.echo(f"Could not connect to {url} - is the server running?")
    except Exception as e:
        click.echo(f"Error: {e}")


# Export command

@main.command("export")
@click.option("--type", "-t", "obj_type", help="Filter by object type (event, learning, reflection)")
@click.option("--output", "-o", type=click.Path(), help="Output file (default: stdout)")
@click.option("--url", default="http://localhost:6001", help="PCP node URL")
def export_data(obj_type, output, url):
    """Export all objects as JSONL."""
    import httpx

    try:
        with httpx.Client(base_url=url, timeout=60.0) as client:
            # Get admin token
            resp = client.post("/api/token", json={
                "subject": "cli-admin",
                "scopes": ["pcp:admin"],
                "hours": 1,
            })
            resp.raise_for_status()
            token = resp.json()["token"]

            params = {"type": obj_type} if obj_type else {}

            with client.stream(
                "GET",
                "/api/export",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            ) as resp:
                resp.raise_for_status()

                out = open(output, "w") if output else None
                try:
                    count = 0
                    for line in resp.iter_lines():
                        if line:
                            if out:
                                out.write(line + "\n")
                            else:
                                click.echo(line)
                            count += 1
                finally:
                    if out:
                        out.close()

            if output:
                click.echo(f"Exported {count} objects to {output}", err=True)
            else:
                click.echo(f"\n# Exported {count} objects", err=True)

    except httpx.ConnectError:
        click.echo(f"Could not connect to {url} - is the server running?")
    except Exception as e:
        click.echo(f"Error: {e}")


# Status command

@main.command()
@click.pass_context
def status(ctx):
    """Show PCP node status."""
    data_dir = ctx.obj["data_dir"]

    click.echo("PCP Node Status")
    click.echo("-" * 40)
    click.echo(f"Data directory: {data_dir}")

    if data_dir.exists():
        click.echo(f"  exists: yes")

        # Count objects
        objects_file = data_dir / "objects.jsonl"
        if objects_file.exists():
            with open(objects_file) as f:
                count = sum(1 for _ in f)
            click.echo(f"  objects: {count}")
        else:
            click.echo("  objects: 0")

        # Check identity
        identity_file = data_dir / "identity.json"
        click.echo(f"  identity: {'set' if identity_file.exists() else 'not set'}")
    else:
        click.echo("  exists: no (run 'pcp server start' to create)")


if __name__ == "__main__":
    main()
