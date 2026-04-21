"""open-teams: A multi-agent collaborative coding system."""

from setuptools import setup, find_packages

setup(
    name="open-teams",
    version="0.1.0",
    description="Multi-agent collaborative coding system",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "anthropic>=0.40.0",
        "openai>=1.0.0",
        "pypdf>=4.0.0",
    ],
    entry_points={
        "console_scripts": [
            "open-teams=open_teams.main:main",
        ],
    },
)
