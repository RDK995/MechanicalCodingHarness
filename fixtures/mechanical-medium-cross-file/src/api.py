from .pricing import total


def quote(subtotal, tax_rate):
    return total(subtotal)
