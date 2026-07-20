import sys
import os

from . import harness
from . import vc as vc_mod


def main():
    config = harness.get_config()

    if len(sys.argv) < 2:
        print("Usage: bid.py <command> [args]")
        print("Commands:")
        print("  init \"USER TASK\"    Initialize a new BID project")
        print("  run                  Run or continue the project")
        print("  status               Show project status")
        print("  resume               Alias for run")
        print("  vc log               Show version control log")
        print("  vc rollback <state>  Rollback to a named state")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "init":
        if len(sys.argv) < 3:
            print("Usage: bid.py init \"USER TASK\"")
            sys.exit(1)
        task = " ".join(sys.argv[2:])
        print(f"Initializing project with task: {task}")
        result = harness.init_project(task, config)
        if result["status"] == "success":
            print("Project initialized. Use 'bid.py run' to start working.")
        else:
            print(f"Init failed: {result.get('reason', 'unknown')}")
            sys.exit(1)

    elif cmd == "run" or cmd == "resume":
        ws = config["workspace"]
        bid_dir = os.path.join(ws, ".bid")
        if not os.path.exists(bid_dir):
            print("No BID project found. Use 'bid.py init' first.")
            sys.exit(1)
        try:
            result = harness.run_project(config)
            if result["status"] == "done":
                print("Task completed!")
            elif result["status"] == "paused":
                print("Project paused. Use 'bid.py run' to continue.")
            else:
                print(f"Run failed: {result.get('reason', 'unknown')}")
                sys.exit(1)
        except KeyboardInterrupt:
            print("\nInterrupted. Last VC state preserved.")

    elif cmd == "status":
        harness.show_status(config)

    elif cmd == "vc":
        if len(sys.argv) < 3:
            print("Usage: bid.py vc <log|rollback> [args]")
            sys.exit(1)
        sub = sys.argv[2]
        vc_sys = vc_mod.VersionControl(config["workspace"])
        if sub == "log":
            print(vc_sys.get_log())
        elif sub == "rollback":
            if len(sys.argv) < 4:
                print("Usage: bid.py vc rollback <state>")
                sys.exit(1)
            state = sys.argv[3]
            vc_sys.restore(state)
            print(f"Rolled back to {state}.")
        else:
            print(f"Unknown vc command: {sub}")
            sys.exit(1)

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
