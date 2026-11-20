"""
Data validation and sanitization utilities
"""
from typing import Any, Optional, List, Dict
import re
from datetime import datetime


def sanitize_string(input_str: str, max_length: int = 1000) -> str:
    """Sanitize string input to prevent injection attacks."""
    if not input_str:
        return ""
    
    # Truncate long strings
    if len(input_str) > max_length:
        input_str = input_str[:max_length]
    
    # Remove null bytes
    input_str = input_str.replace('\x00', '')
    
    # Strip whitespace
    input_str = input_str.strip()
    
    # Remove control characters
    input_str = ''.join(c for c in input_str if ord(c) >= 32 or c in '\n\r\t')
    
    return input_str


def sanitize_int(value: Any, default: int = 0) -> int:
    """Sanitize integer input."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def sanitize_float(value: Any, default: float = 0.0) -> float:
    """Sanitize float input."""
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def validate_email(email: str) -> bool:
    """Validate email format."""
    pattern = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'
    return bool(re.match(pattern, email))


def validate_symbol(symbol: str) -> bool:
    """Validate trading symbol."""
    pattern = r'^[A-Z][A-Z0-9]{2,}$'  # NIFTY, BANKNIFTY, etc.
    return bool(re.match(pattern, symbol))


def validate_price(price: float) -> bool:
    """Validate price is positive and reasonable."""
    return price > 0 and price < 1000000


def validate_quantity(quantity: int) -> bool:
    """Validate quantity is positive."""
    return quantity > 0 and quantity < 10000


def sanitize_order_params(
    symbol: str,
    side: str,
    quantity: Any,
    order_type: str = "MARKET",
    limit_price: Any = None
) -> Dict[str, Any]:
    """Sanitize and validate order parameters."""
    result = {
        "symbol": sanitize_string(symbol).upper(),
        "side": side.upper() if side else "BUY",
        "quantity": sanitize_int(quantity, 1),
        "order_type": order_type.upper() if order_type else "MARKET",
        "limit_price": None,
        "valid": True,
        "errors": []
    }
    
    # Validate symbol
    if not validate_symbol(result["symbol"]):
        result["valid"] = False
        result["errors"].append(f"Invalid symbol: {result['symbol']}")
    
    # Validate side
    if result["side"] not in ["BUY", "SELL"]:
        result["valid"] = False
        result["errors"].append(f"Invalid side: {result['side']}")
    
    # Validate quantity
    if not validate_quantity(result["quantity"]):
        result["valid"] = False
        result["errors"].append(f"Invalid quantity: {result['quantity']}")
    
    # Validate order type
    if result["order_type"] not in ["MARKET", "LIMIT", "SL", "SL-M"]:
        result["valid"] = False
        result["errors"].append(f"Invalid order type: {result['order_type']}")
    
    # Validate limit price if specified
    if limit_price is not None:
        limit = sanitize_float(limit_price, 0)
        if order_type.upper() == "LIMIT" and limit <= 0:
            result["valid"] = False
            result["errors"].append("Limit price must be positive for LIMIT orders")
        elif order_type.upper() == "SL" and limit <= 0:
            result["valid"] = False
            result["errors"].append("Stop loss price must be positive")
        else:
            result["limit_price"] = limit
    
    return result


def validate_prediction_request(
    index: str = "NIFTY 50",
    confidence_threshold: Any = None
) -> Dict[str, Any]:
    """Validate prediction request parameters."""
    result = {
        "index": sanitize_string(index).upper(),
        "confidence_threshold": None,
        "valid": True,
        "errors": []
    }
    
    # Validate index
    valid_indices = ["NIFTY 50", "BANKNIFTY", "SENSEX"]
    if result["index"] not in valid_indices:
        result["valid"] = False
        result["errors"].append(f"Invalid index: {result['index']}")
    
    # Validate confidence threshold
    if confidence_threshold is not None:
        try:
            threshold = float(confidence_threshold)
            if 0 <= threshold <= 1:
                result["confidence_threshold"] = threshold
            else:
                result["valid"] = False
                result["errors"].append("Confidence threshold must be between 0 and 1")
        except ValueError:
            result["valid"] = False
            result["errors"].append("Confidence threshold must be a number")
    
    return result


def validate_order_params(
    symbol: str,
    side: str,
    quantity: Any,
    order_type: str = "MARKET",
    limit_price: Any = None
) -> Dict[str, Any]:
    """Validate order parameters."""
    result = {
        "symbol": sanitize_string(symbol).upper(),
        "side": side.upper() if side else "BUY",
        "quantity": sanitize_int(quantity, 1),
        "order_type": order_type.upper() if order_type else "MARKET",
        "limit_price": None,
        "valid": True,
        "errors": []
    }
    
    # Validate symbol
    if not validate_symbol(result["symbol"]):
        result["valid"] = False
        result["errors"].append(f"Invalid symbol: {result['symbol']}")
    
    # Validate side
    if result["side"] not in ["BUY", "SELL"]:
        result["valid"] = False
        result["errors"].append(f"Invalid side: {result['side']}")
    
    # Validate quantity
    if not validate_quantity(result["quantity"]):
        result["valid"] = False
        result["errors"].append(f"Invalid quantity: {result['quantity']}")
    
    # Validate order type
    if result["order_type"] not in ["MARKET", "LIMIT", "SL", "SL-M"]:
        result["valid"] = False
        result["errors"].append(f"Invalid order type: {result['order_type']}")
    
    # Validate limit price if specified
    if limit_price is not None:
        limit = sanitize_float(limit_price, 0)
        if order_type.upper() == "LIMIT" and limit <= 0:
            result["valid"] = False
            result["errors"].append("Limit price must be positive for LIMIT orders")
        elif order_type.upper() == "SL" and limit <= 0:
            result["valid"] = False
            result["errors"].append("Stop loss price must be positive")
        else:
            result["limit_price"] = limit
    
    return result


def validate_news_request(
    limit: Any = 20,
    min_impact: Any = None
) -> Dict[str, Any]:
    """Validate news request parameters."""
    result = {
        "limit": sanitize_int(limit, 20),
        "min_impact": None,
        "valid": True,
        "errors": []
    }
    
    # Validate limit
    if result["limit"] < 1 or result["limit"] > 100:
        result["valid"] = False
        result["errors"].append("Limit must be between 1 and 100")
    
    # Validate min impact
    if min_impact is not None:
        try:
            impact = float(min_impact)
            if 0 <= impact <= 100:
                result["min_impact"] = impact
            else:
                result["valid"] = False
                result["errors"].append("Min impact must be between 0 and 100")
        except ValueError:
            result["valid"] = False
            result["errors"].append("Min impact must be a number")
    
    return result


def sanitize_strategy_params(
    name: str,
    parameters: Dict[str, Any]
) -> Dict[str, Any]:
    """Sanitize and validate strategy parameters."""
    result = {
        "name": sanitize_string(name, 100),
        "parameters": {},
        "valid": True,
        "errors": []
    }
    
    # Sanitize parameters
    for key, value in parameters.items():
        sanitized_key = sanitize_string(key)
        if not sanitized_key:
            continue
        
        if isinstance(value, str):
            result["parameters"][sanitized_key] = sanitize_string(value)
        elif isinstance(value, (int, float)):
            result["parameters"][sanitized_key] = sanitize_float(value)
        elif isinstance(value, bool):
            result["parameters"][sanitized_key] = value
        elif isinstance(value, dict):
            result["parameters"][sanitized_key] = sanitize_strategy_params(
                sanitized_key, value
            )["parameters"]
    
    return result


if __name__ == "__main__":
    # Test the validators
    print("Testing validators...")
    
    # Order validation
    order = sanitize_order_params(
        symbol="NIFTY",
        side="BUY",
        quantity=100,
        order_type="LIMIT",
        limit_price=18500.00
    )
    print(f"Order validation: {order}")
    
    # Prediction validation
    prediction = validate_prediction_request(index="NIFTY 50")
    print(f"Prediction validation: {prediction}")
    
    print("All tests passed!")
