import sys
import os

def run_tests():
    print("Starting NeuroCore Test Runner...")

    original_cwd = os.getcwd()
    try:
        # Set the working directory to the project root for imports to work
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # Explicitly add project root to sys.path to ensure modules are discoverable
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        os.chdir(project_root)

        # Run pytest programmatically to avoid PATH issues
        try:
            import pytest
        except ImportError:
            print("\nError: 'pytest' is not installed. Please install it with 'pip install pytest'")
            sys.exit(1)

        # Allow passing command-line arguments to pytest. Default to running all tests verbosely.
        pytest_args = sys.argv[1:]
        
        # Add coverage arguments if requested and the plugin is available
        if "--coverage" in pytest_args:
            try:
                import pytest_cov
            except ImportError:
                print("\nError: 'pytest-cov' is not installed. Please install it with 'pip install pytest-cov' to run coverage reports.")
                sys.exit(1)
            pytest_args.remove("--coverage")
            pytest_args = ["--cov=core", "--cov=modules", "--cov-report=term-missing"] + pytest_args
        elif not pytest_args:
            pytest_args = [".", "-v", "--ignore=scripts", "--ignore=test_results.txt"]

        # pytest.main returns an exit code that we can check
        exit_code = pytest.main(pytest_args)

        if exit_code == 0:
            print("\nAll tests passed successfully!")
        else:
            print(f"\nTests failed with exit code {exit_code}")

        sys.exit(exit_code)

    finally:
        # Ensure the original working directory is restored
        os.chdir(original_cwd)

if __name__ == "__main__":
    run_tests()
