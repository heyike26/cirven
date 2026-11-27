"""Bounded exact Decimal arithmetic, independent of the caller's context."""
from decimal import (Context, Decimal, Inexact, InvalidOperation, MAX_EMAX, MIN_EMIN,
                     ROUND_HALF_EVEN, localcontext)

MAX_DECIMAL_SPAN = 4096


def bounded_decimal(value):
    if isinstance(value, str) and len(value) > MAX_DECIMAL_SPAN:
        raise ValueError("SDK_DECIMAL_UNSUPPORTED")
    try:
        number = Decimal(value)
        parts = number.as_tuple()
        if (not number.is_finite() or len(parts.digits) > MAX_DECIMAL_SPAN
                or abs(parts.exponent) > MAX_DECIMAL_SPAN):
            raise ValueError
        return number
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("SDK_DECIMAL_UNSUPPORTED") from None


def exact_sum(values):
    numbers = [bounded_decimal(value) for value in values]
    if not numbers:
        return Decimal("0")
    minimum_exponent = min(number.as_tuple().exponent for number in numbers)
    maximum_adjusted = max((number.adjusted() for number in numbers if not number.is_zero()), default=minimum_exponent)
    precision = max(1, maximum_adjusted - minimum_exponent + 1) + len(str(len(numbers))) + 1
    # Reject before Context/coefficient allocation, never approximate a sum.
    if precision > MAX_DECIMAL_SPAN:
        raise ValueError("SDK_DECIMAL_UNSUPPORTED")
    context = Context(prec=precision, rounding=ROUND_HALF_EVEN, Emin=MIN_EMIN, Emax=MAX_EMAX, clamp=0)
    context.traps[Inexact] = True
    with localcontext(context):
        return sum(numbers, Decimal("0"))


def exact_difference(left, right):
    # Unary '-' applies the ambient context; copy_negate is exact.
    return exact_sum((bounded_decimal(left), bounded_decimal(right).copy_negate()))
