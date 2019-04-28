import setuptools


def long_description():
    with open('README.md', 'r') as file:
        return file.read()


setuptools.setup(
    name='lowhaio',
    version='0.0.25',
    author='Michal Charemza',
    author_email='michal@charemza.name',
    description='Lightweight Python asyncio HTTP/1.1 client. ',
    long_description=long_description(),
    long_description_content_type='text/markdown',
    url='https://github.com/michalc/lowhaio',
    py_modules=[
        'lowhaio',
    ],
    python_requires='>=3.6.3',
    install_requires=[
        'aiodnsresolver==0.0.116',
    ],
    test_suite='test',
    tests_require=[
        'aiohttp~=3.5.4',
    ],
    classifiers=[
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Framework :: AsyncIO',
    ],
)
