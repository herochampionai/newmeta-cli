"""Sample Plugin - Calculator tools"""

def add(a: float, b: float) -> float:
    """Add two numbers"""
    return a + b

def subtract(a: float, b: float) -> float:
    """Subtract b from a"""
    return a - b

def multiply(a: float, b: float) -> float:
    """Multiply two numbers"""
    return a * b

def divide(a: float, b: float) -> str:
    """Divide a by b, returns Raid Wipe if division by zero"""
    if b == 0:
        return "Raid Wipe: Division by zero"
    return a / b

def power(base: float, exponent: float) -> float:
    """Calculate base raised to exponent"""
    return base ** exponent

def sqrt(number: float) -> float:
    """Calculate square root"""
    return number ** 0.5

def modulo(a: float, b: float) -> float:
    """Calculate a modulo b"""
    return a % b

add._is_tool = True
add._params = {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}, "required": ["a", "b"]}

subtract._is_tool = True
subtract._params = {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}, "required": ["a", "b"]}

multiply._is_tool = True
multiply._params = {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}, "required": ["a", "b"]}

divide._is_tool = True
divide._params = {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}, "required": ["a", "b"]}

power._is_tool = True
power._params = {"type": "object", "properties": {"base": {"type": "number"}, "exponent": {"type": "number"}}, "required": ["base", "exponent"]}

sqrt._is_tool = True
sqrt._params = {"type": "object", "properties": {"number": {"type": "number"}}, "required": ["number"]}

modulo._is_tool = True
modulo._params = {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}, "required": ["a", "b"]}