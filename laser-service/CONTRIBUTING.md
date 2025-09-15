# Contributing to LDM302 Laser Control API

Thank you for your interest in contributing to this project! 🎉

## 🚀 Getting Started

### Prerequisites
- Python 3.11+
- Git
- Basic knowledge of FastAPI and asyncio

### Development Setup
1. **Fork and clone the repository**
```bash
git clone https://github.com/yourusername/ldm302-laser-api.git
cd ldm302-laser-api
```

2. **Create a virtual environment**
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. **Install dependencies**
```bash
pip install -r requirements.txt
```

4. **Set up environment**
```bash
cp .env.example .env
# Edit .env with your configuration
```

5. **Run tests to verify setup**
```bash
pytest
```

## 🔧 Development Guidelines

### Code Style
- Follow **PEP 8** coding standards
- Use **async/await** patterns consistently
- Add **type hints** to all functions
- Document functions with **docstrings**
- Keep functions focused and small

### Example Code Style
```python
async def process_laser_data(
    laser_id: int, 
    data: Dict[str, Any],
    timeout: float = 5.0
) -> Optional[MeasurementResult]:
    """
    Process laser measurement data with error handling.
    
    Args:
        laser_id: Unique laser device identifier
        data: Raw measurement data from device
        timeout: Operation timeout in seconds
        
    Returns:
        Processed measurement result or None if failed
        
    Raises:
        ConnectionError: If laser device is not accessible
        ValidationError: If data format is invalid
    """
    try:
        # Implementation here
        pass
    except Exception as e:
        logger.error(f"Failed to process laser {laser_id} data: {e}")
        return None
```

### Testing
- Write **unit tests** for new functions
- Add **integration tests** for API endpoints
- Use **pytest** and **pytest-asyncio**
- Aim for **80%+ code coverage**

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=core --cov=api --cov=services

# Run specific test file
pytest tests/test_laser_device.py -v
```

### Git Workflow
1. **Create a feature branch**
```bash
git checkout -b feature/your-feature-name
```

2. **Make your changes**
- Keep commits small and focused
- Write clear commit messages
- Test your changes locally

3. **Commit your changes**
```bash
git add .
git commit -m "feat: add laser reconnection logic

- Implement automatic reconnection on connection loss
- Add exponential backoff for retry attempts  
- Update tests for new reconnection behavior"
```

4. **Push and create PR**
```bash
git push origin feature/your-feature-name
```

### Commit Message Format
Use conventional commits format:
- `feat:` - New features
- `fix:` - Bug fixes
- `docs:` - Documentation changes
- `style:` - Code style changes (no logic changes)
- `refactor:` - Code refactoring
- `test:` - Adding or updating tests
- `chore:` - Maintenance tasks

## 🐛 Reporting Issues

### Bug Reports
When reporting bugs, please include:
- **Clear description** of the issue
- **Steps to reproduce** the problem
- **Expected vs actual behavior**
- **Environment details** (OS, Python version, etc.)
- **Log output** if applicable

### Feature Requests
For new features, please:
- **Describe the use case** clearly
- **Explain the benefit** to users
- **Consider implementation** complexity
- **Check for existing** similar features

## 🔍 Code Review Process

### What We Look For
- **Functionality**: Does the code work as intended?
- **Security**: Are there any security vulnerabilities?
- **Performance**: Is the code efficient?
- **Maintainability**: Is the code easy to understand and modify?
- **Testing**: Are there adequate tests?
- **Documentation**: Is the code well documented?

### Review Checklist
- [ ] Code follows project style guidelines
- [ ] All tests pass
- [ ] New features have tests
- [ ] Documentation is updated
- [ ] No security vulnerabilities introduced
- [ ] Performance impact considered
- [ ] Backward compatibility maintained

## 🎯 Areas for Contribution

### High Priority
- **Error handling improvements**
- **Performance optimizations**
- **Additional device protocols**
- **Monitoring and alerting**
- **Security enhancements**

### Medium Priority
- **WebSocket enhancements**
- **Database optimizations**
- **Configuration improvements**
- **Testing improvements**
- **Documentation updates**

### Good First Issues
- **Adding type hints** to existing functions
- **Improving error messages**
- **Writing additional tests**
- **Documentation improvements**
- **Code style fixes**

## 📚 Development Resources

### Architecture Docs
- **FastAPI**: https://fastapi.tiangolo.com/
- **asyncio**: https://docs.python.org/3/library/asyncio.html
- **AsyncPG**: https://magicstack.github.io/asyncpg/current/
- **WebSockets**: https://websockets.readthedocs.io/

### Project Structure
```
├── api/                 # REST API routes and handlers
├── core/                # Core business logic and device management
├── models/              # Pydantic data models
├── services/            # External service integrations
├── simulator/           # Device simulators for testing
├── tests/               # Test suites
├── docs/                # Documentation and resources
└── main.py              # Application entry point
```

### Key Components
- **MultiLaserManager**: Manages multiple laser devices
- **LaserDevice**: Individual device connection and control
- **WebSocketManager**: Real-time data streaming
- **DatabaseService**: Data persistence and retrieval
- **CircuitBreaker**: Fault tolerance patterns

## ✅ Pull Request Checklist

Before submitting a PR, ensure:
- [ ] Code follows project style guidelines
- [ ] All existing tests pass
- [ ] New features have corresponding tests
- [ ] Documentation is updated (if applicable)
- [ ] Commit messages follow conventional format
- [ ] PR description clearly explains changes
- [ ] No merge conflicts
- [ ] Branch is up to date with main

## 🤝 Community

### Communication
- **GitHub Issues**: For bug reports and feature requests
- **GitHub Discussions**: For general questions and ideas
- **Pull Requests**: For code contributions

### Code of Conduct
- Be respectful and inclusive
- Provide constructive feedback
- Help others learn and grow
- Focus on the technical merits
- Maintain professionalism

## 🎉 Recognition

Contributors will be recognized in:
- **README.md** contributors section
- **CHANGELOG.md** for their contributions
- **GitHub releases** acknowledgments

Thank you for helping make this project better! 🚀