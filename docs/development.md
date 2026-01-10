# Development

This section provides instructions for setting up the development environment
for contributors.

## Prerequisites

Ensure you have the following installed on your system:

- Python 3.12+
- `pip`
- `virtualenv`

To install these on Ubuntu, run:

```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv -y
```

## Setup Instructions

1. **Clone the Repository**

   ```bash
   git clone git@github.com:MoonyFringers/ladon.git
   cd ladon
   ```

2. **Create a Virtual Environment**

   Use a virtual environment to isolate dependencies:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **Install Dependencies**

   Install the required Python packages from `requirements.txt`:

   ```bash
   pip install --upgrade pip
   pip install -r src/requirements.txt -r src/requirements-dev.txt
   ```

   For optional local strictness and commit tooling, see
   [Local "Shadow" Setup](local-shadow-setup.md).

## Lints & Checks

1. **pre-commit**

   Run pre-commit manually:

   ```bash
   pre-commit run --all-files
   ```

   Install pre-commit:

   ```bash
   pre-commit install
   ```

   For an optional stricter local config and commit-message tooling, see
   [Local "Shadow" Setup](local-shadow-setup.md).

2. **Black**

   Format your code manually:

   ```bash
   black src
   ```

3. **Isort**

   Sort imports manually by running:

   ```bash
   isort src
   ```

4. **Pyright**

   Run type checking:

   ```bash
   pyright src
   ```

5. **Pytest**

   Run tests with coverage:

   ```bash
   cd src
   pytest
   ```
