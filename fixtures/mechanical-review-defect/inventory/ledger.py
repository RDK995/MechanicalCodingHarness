class InsufficientStock(Exception):
    pass


class Ledger:
    def __init__(self):
        self._on_hand = 0
        self._history = []

    @property
    def on_hand(self):
        return self._on_hand

    def history(self):
        return tuple(self._history)

    def apply(self, amount, reason):
        next_count = self._on_hand + amount
        if next_count < 0:
            raise InsufficientStock(
                f"withdrawal of {-amount} exceeds {self._on_hand} on hand"
            )
        self._on_hand = next_count
        self._history.append((amount, reason))
