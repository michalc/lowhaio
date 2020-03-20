import setuptools


def long_description():
    with open('README.md', 'r') as file:
        return file.read()


setuptools.setup(
    name='lowhaio',
    version='0.0.88',
    author='Michal Charemza',
    author_email='michal@charemza.name',
    description='Lightweight Python asyncio HTTP client',
    long_description=long_description(),
    long_description_content_type='text/markdown',
    url='https://github.com/michalc/lowhaio',
    py_modules=[
        'lowhaio',
    ],
    python_requires='>=3.6.3',
    install_requires=[
        'aiodnsresolver>=0.0.149',
    ],
    test_suite='test',
    tests_require=[
        'aiofastforward~=0.0.24',
        'aiohttp~=3.5.4',
    ],
    classifiers=[
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Framework :: AsyncIO',
    ],
    keywords=[
        'http/1.1',
    ],
)
