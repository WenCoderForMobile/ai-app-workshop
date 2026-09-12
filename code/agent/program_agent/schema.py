"""ProgramIR whitelist used by compiler and Runtime v1."""

ALLOWED_COMPONENTS = {
    "Column",
    "Row",
    "Text",
    "Button",
    "TextInput",
    "Checkbox",
    "List",
    "Spacer",
    "Dialog",
}

ALLOWED_ACTIONS = {
    "setState",
    "showMessage",
    "navigate",
    "finish",
    "listAppend",
    "listRemove",
}

ALLOWED_STATE_TYPES = {"bool", "int64", "string", "list"}
ALLOWED_PERSISTENCE = {"session", "local"}
ALLOWED_OPS = {"set", "inc", "dec", "toggle"}

MAX_NODES = 80
MAX_SCREENS = 6
MAX_STATE_KEYS = 20
