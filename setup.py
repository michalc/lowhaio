import setuptools


def long_description():
    with open('README.md', 'r') as file:
        return file.read()


setuptools.setup(
    name='lowhaio',
    version='0.0.2',
    author='Michal Charemza',
    author_email='michal@charemza.name',
    description='Lightweight and dependency-free Python asyncio HTTP/1.1 client. ',
    long_description=long_description(),
    long_description_content_type='text/markdown',
    url='https://github.com/michalc/lowhaio',
    py_modules=[
        'lowhaio',
    ],
    python_requires='>=3.7.0',
    test_suite='test',
    classifiers=[
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Framework :: AsyncIO',
    ],
)
