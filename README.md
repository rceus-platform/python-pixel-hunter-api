# Pixel Hunter API

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/release/python-3120/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.116+-00a393.svg)](https://fastapi.tiangolo.com)
[![uv](https://img.shields.io/badge/uv-fast-ff0000.svg)](https://github.com/astral-sh/uv)

## Overview

The **Pixel Hunter API** is a high-performance backend service designed for HD image searching, fetching, and processing. Built with **FastAPI** and **Python 3.12+**, it provides a robust interface for interacting with various image sources and performing automated web scraping and image extraction.

This API serves as the backbone for the [React Pixel Hunter UI](https://github.com/rceus-platform/react-pixel-hunter-ui).

## Key Features

- **High-Definition Image Search**: Query and retrieve high-quality images from multiple sources.
- **Advanced Scraping Engine**: Utilizes `cloudscraper`, `selenium`, and `selectolax` to bypass anti-bot protections and extract image metadata efficiently.
- **Image Processing**: Integrates `Pillow` for on-the-fly image validation and processing.
- **Asynchronous Architecture**: Built on FastAPI for high-concurrency, non-blocking I/O operations.

## Tech Stack

- **Framework**: [FastAPI](https://fastapi.tiangolo.com/)
- **Runtime**: [Python 3.12+](https://www.python.org/)
- **Scraping**: `cloudscraper`, `selenium`, `selectolax`
- **Image Processing**: `Pillow`
- **Testing**: `pytest`, `pytest-asyncio`

## Getting Started

### Prerequisites

- **Python**: 3.12 or higher
- **uv**: Astral's high-speed Python package manager (`pip install uv`)

### Installation

1. **Clone the repository:**

   ```bash
   git clone <repository_url>
   cd python-pixel-hunter-api/application-source
   ```

2. **Sync dependencies:**
   ```bash
   uv sync
   ```

### Configuration

Create a `.env` file in the `application-source` directory based on the provided template (do not commit this file to version control).

```env
# Application Settings
PORT=8000
ENVIRONMENT=development

# Security
API_SECRET_KEY=your_secure_api_key_here
```

### Running the Service

Start the FastAPI Backend:
```bash
cd application-source
uv run uvicorn app.main:app --reload --port 8000
```

The API documentation (Swagger UI) will be available at `http://localhost:8000/docs`.

### Testing

Run the test suite using pytest:
```bash
uv run pytest
```

## Security Considerations

- **Secrets Management**: Never commit `.env` files or hardcode sensitive credentials. Use a secure secret manager in production.
- **Web Scraping Rate Limits**: Ensure compliance with target website rate limits and terms of service. Implement appropriate delays or proxy rotation if necessary.

## License

This project is licensed under MIT License.
