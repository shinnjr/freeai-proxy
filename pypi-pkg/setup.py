from setuptools import setup, find_packages

setup(
    name="freeai-proxy",
    version="1.0.0",
    description="Zero-cost AI model routing proxy - automatically selects cheapest capable model per request",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author="FreeAI Proxy",
    url="https://freeai-proxy.pages.dev",
    packages=find_packages(),
    install_requires=["fastapi", "uvicorn", "httpx"],
    entry_points={
        "console_scripts": [
            "freeai-proxy=freeai_proxy.cli:main",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
    ],
    python_requires=">=3.10",
)
