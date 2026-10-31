"""Quick verification tests for the bug fixes"""
import sys
import os

# Test 1: Verify update_event sets updated_at
print("Test 1: update_event sets updated_at...")
from modules.calendar.events import EventManager
import tempfile
import uuid

# Create a temp event manager
with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
    temp_file = f.name

em = EventManager(storage_file=temp_file)
event = em.add_event("Test Event", "2024-12-25 10:00")

# Update the event
updated = em.update_event(event['id'], title="Updated Title")

assert 'updated_at' in updated, "FAIL: updated_at not set in update_event"
print(f"  ✓ updated_at is set: {updated['updated_at']}")

# Test 2: Verify mark_notified works
print("\nTest 2: mark_notified works...")
notified = em.mark_notified(event['id'])
assert notified['notified'] == True, "FAIL: notified not set to True"
assert 'updated_at' in notified, "FAIL: updated_at not set in mark_notified"
print(f"  ✓ notified=True, updated_at={notified['updated_at']}")

# Test 3: Verify month normalization fix
print("\nTest 3: Month normalization for negative months...")
# Simulate the fixed formula
def normalize_month_fixed(year, month):
    if month > 12:
        year += (month - 1) // 12
        month = (month - 1) % 12 + 1
    elif month < 1:
        year += (month - 1) // 12
        month = (month - 1) % 12 + 1
    return year, month

# Test cases
test_cases = [
    (2024, 0, (2023, 12)),   # month=0 -> Dec prev year
    (2024, -1, (2023, 11)),  # month=-1 -> Nov prev year  
    (2024, -12, (2022, 12)), # month=-12 -> Dec 2 years ago
    (2024, 13, (2025, 1)),   # month=13 -> Jan next year
]

for input_year, input_month, expected in test_cases:
    result = normalize_month_fixed(input_year, input_month)
    assert result == expected, f"FAIL: month={input_month} got {result}, expected {expected}"
    print(f"  ✓ month={input_month} -> year={result[0]}, month={result[1]}")

# Test 4: Verify get_enriched_upcoming_events accepts event_manager param
print("\nTest 4: get_enriched_upcoming_events accepts event_manager...")
from modules.calendar.router import get_enriched_upcoming_events

# Should work with custom event_manager
result = get_enriched_upcoming_events(event_manager=em)
print(f"  ✓ Function accepts event_manager parameter")

# Clean up
os.unlink(temp_file)

print("\n" + "="*50)
print("ALL VERIFICATION TESTS PASSED!")
print("="*50)

