import os
import sys

from . import harness
from . import vc as vc_mod


def main():
    config = harness.get_config()

    if len(sys.argv) < 2:
        print("Usage: bid.py <command> [args]")
        print("Commands:")
        print('  init "USER TASK"    Initialize a new BID project')
        print("  run                  Run or continue the project")
        print("  status               Show project status")
        print("  resume               Alias for run")
        print("  vc log               Show version control log")
        print("  vc rollback <state>  Rollback to a named state")
        sys.exit(1)

    command = sys.argv[1]

    if command == "init":
        if len(sys.argv) < 3:
            print('Usage: bid.py init "USER TASK"')
            sys.exit(1)
        task = " ".join(sys.argv[2:])
        print(f"Initializing project with task: {task}")
        try:
            result = harness.init_project(task, config)
        except KeyboardInterrupt:
            print("\nInitialization interrupted. Previous workspace restored.")
            return
        if result["status"] == "success":
            print("Project initialized. Use 'bid.py run' to start working.")
        else:
            print(f"Init failed: {result.get('reason', 'unknown')}")
            sys.exit(1)
        return

    if command in ("run", "resume"):
        workspace = config["workspace"]
        if not os.path.exists(os.path.join(workspace, ".bid")):
            print("No BID project found. Use 'bid.py init' first.")
            sys.exit(1)
        try:
            result = harness.run_project(config)
        except KeyboardInterrupt:
            print("\nInterrupted. Last VC state preserved.")
            return
        if result["status"] == "done":
            print("Task completed!")
        elif result["status"] == "paused":
            print(f"Project paused. {result.get('reason', 'Use bid.py run to continue.')}")
        else:
            print(f"Run failed: {result.get('reason', 'unknown')}")
            sys.exit(1)
        return

    if command == "status":
        harness.show_status(config)
        return

    if command == "vc":
        if len(sys.argv) < 3:
            print("Usage: bid.py vc <log|rollback> [args]")
            sys.exit(1)
        subcommand = sys.argv[2]
        system = vc_mod.VersionControl(config["workspace"])
        if subcommand == "log":
            print(system.get_log())
            return
        if subcommand == "rollback":
            if len(sys.argv) < 4:
                print("Usage: bid.py vc rollback <state>")
                sys.exit(1)
            state = sys.argv[3]
            try:
                with harness._project_run_lock(config["workspace"]):
                    system.restore(state)
            except RuntimeError as exc:
                print(f"Rollback refused: {exc}")
                sys.exit(1)
            print(f"Rolled back to {state}.")
            return
        print(f"Unknown vc command: {subcommand}")
        sys.exit(1)

    print(f"Unknown command: {command}")
    sys.exit(1)
