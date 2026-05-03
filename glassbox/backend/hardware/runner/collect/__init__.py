"""collect/ -- hardware I/O layer.

Talks to the harness over USB CDC and produces parquet trace files.
Everything below this line touches a real serial port; everything above
it is pure numpy / pandas.
"""
