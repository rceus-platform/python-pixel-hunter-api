
import os
import sys

if os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")) not in sys.path:
    sys.path.insert(
        0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    )

def main():
    print("Application started.")

if __name__ == "__main__":
    main()
    print("Execution completed.")
