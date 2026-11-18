"""Small inventory tracker used as a GhostWriter demo file."""
import json
from dataclasses import dataclass, field
from pathlib import Path

LOW_STOCK = 5


@dataclass
class Item:
    name: str
    price: float
    quantity: int = 0
    tags: list[str] = field(default_factory=list)

    def total(self) -> float:
        # Value of everything on the shelf.
        return round(self.price * self.quantity, 2)


class Inventory:
    def __init__(self, path: Path):
        self.path = path
        self.items: dict[str, Item] = {}

    def add(self, item: Item) -> None:
        if item.name in self.items:
            self.items[item.name].quantity += item.quantity
        else:
            self.items[item.name] = item

    def low_stock(self):
        for item in self.items.values():
            if item.quantity < LOW_STOCK:
                yield item

    def report(self) -> str:
        lines = [f"{'Item':<12}{'Qty':>5}"]
        for name, item in sorted(self.items.items()):
            lines.append(f"{name:<12}{item.quantity:>5}")
        return "\n".join(lines)

    def save(self) -> None:
        data = {
            name: {"price": item.price, "quantity": item.quantity}
            for name, item in self.items.items()
        }
        with open(self.path, "w") as handle:
            json.dump(data, handle, indent=2)


def main():
    inventory = Inventory(Path("stock.json"))
    inventory.add(Item("widget", 2.5, 10, ["small"]))
    inventory.add(Item("gadget", 12.0, 3))
    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            break
        if not line:
            continue
        elif line == "report":
            print(inventory.report())
        else:
            print("Don't know that one")
    inventory.save()


if __name__ == "__main__":
    main()
