def greet(name):
    return f"Hello, {name}!" if name and name.strip() else "Hello, World!"

if __name__ == "__main__":
    print(greet("Bridge"))
