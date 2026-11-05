"""Hand-built payment network with known hubs, loops, pairs and rings (shared by the pandas and Spark tests)."""
import pandas as pd

START = pd.Timestamp('2022-09-01 08:00')


def day(offset):
    # Minute precision, like the source CSV.
    return (START + pd.Timedelta(days=offset)).round('min')


def network_frame():
    rows = []

    # Each account has one bank. Padded codes ('010', '0020') test the
    # leading-zero rule: keys become '10:<account>' and '20:<account>'.
    def bank(account):
        return '0020' if account.startswith(('R', 'G')) else '010'

    def pay(src, dst, when, usd=100.0, label=0):
        rows.append(dict(timestamp=when, from_bank=bank(src), from_account=src, to_bank=bank(dst),
                         to_account=dst, amount_usd=usd, is_laundering=label))

    # Hub H (key 10:H): 10 senders in, 10 receivers out; two labelled payments.
    for i in range(10):
        pay(f'S{i}', 'H', day(0.1 * i), usd=50.0, label=int(i < 2))
        pay('H', f'R{i}', day(1 + 0.1 * i), usd=45.0)
    # A second payment on one hub link merges into the same link.
    pay('S0', 'H', day(2), usd=10.0)
    # High-volume account G: 60 in + 60 out = 120 links > MAX_LINKS.
    for i in range(60):
        pay(f'GI{i}', 'G', day(0.01 * i), usd=5.0)
        pay('G', f'GO{i}', day(1 + 0.01 * i), usd=5.0)
    # Timed loop A -> B -> C -> A (1 and 2 days apart).
    pay('A', 'B', day(0), usd=1000.0, label=1)
    pay('B', 'C', day(1), usd=990.0)
    pay('C', 'A', day(2), usd=980.0)
    # Too slow: D -> E -> F -> D spans 9 days in every rotation.
    pay('D', 'E', day(0))
    pay('E', 'F', day(5))
    pay('F', 'D', day(9))
    # Only in order when started from Z: Z -> X (d1), X -> Y (d5), Y -> Z (d6).
    pay('X', 'Y', day(5))
    pay('Y', 'Z', day(6))
    pay('Z', 'X', day(1))
    # Back-and-forth pair P <-> Q within 2 days; U <-> V too slow (9 days).
    pay('P', 'Q', day(1))
    pay('Q', 'P', day(3))
    pay('U', 'V', day(1))
    pay('V', 'U', day(10))
    # Self-transfers are ignored.
    pay('SELF', 'SELF', day(0))
    pay('SELF', 'SELF', day(1))
    return pd.DataFrame(rows)


def factory(frame, parts=3):
    """Batches factory that splits the frame so partial link merging is exercised."""
    size = -(-len(frame) // parts)

    def batches():
        for start in range(0, len(frame), size):
            yield frame.iloc[start:start + size].reset_index(drop=True)
    return batches
