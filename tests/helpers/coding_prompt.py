from io import StringIO


def assert_numbered_file_inputs(actual: dict[str, list[str]], expected: dict[str, str]) -> None:
    assert actual.keys() == expected.keys()
    for path, source in expected.items():
        rows = actual[path]
        assert isinstance(rows, list)
        original_lines = []
        for number, row in enumerate(rows, start=1):
            prefix, separator, original_line = row.partition("|")
            assert (prefix, separator) == (str(number), "|")
            original_lines.append(original_line)
        assert original_lines == list(StringIO(source, newline=""))
        assert "".join(original_lines) == source
