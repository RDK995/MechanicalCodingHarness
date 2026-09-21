from .taxes import apply_tax


def total(subtotal):
    return apply_tax(subtotal)
