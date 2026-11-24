from cart import total

assert total([(10.0, 2), (5.0, 1)]) == 25.0
assert total([(200.0, 1)], discount=10) == 180.0, "10% off 200 should be 180"
assert total([(100.0, 1)], discount=50) == 50.0, "50% off 100 should be 50"
print("all tests passed")
