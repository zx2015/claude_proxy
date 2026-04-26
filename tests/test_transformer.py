import sys
import os
import traceback

# 将项目根目录添加到 python 路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.transformer.engine import transformer

def test_xml_extraction():
    print("Testing XML extraction...")
    text = "I will list the files now. <tool_code>{\"name\": \"ls\", \"arguments\": {\"path\": \".\"}}</tool_code> Done."
    tools, cleaned = transformer._extract_tools_from_text(text)
    
    assert len(tools) == 1, f"Expected 1 tool, got {len(tools)}"
    assert tools[0]["name"] == "ls", f"Expected name 'ls', got {tools[0]['name']}"
    assert tools[0]["input"] == {"path": "."}
    assert "<tool_code>" not in cleaned
    assert "Done." in cleaned
    print("✓ XML extraction passed")

def test_markdown_json_extraction():
    print("Testing Markdown JSON extraction...")
    text = "Here is the command:\n```json\n{\"command\": \"cat GEMINI.md\", \"tool\": \"bash\"}\n```\nLet me know if you need more."
    tools, cleaned = transformer._extract_tools_from_text(text)
    
    if len(tools) != 1:
        print(f"DEBUG: tools={tools}")
        print(f"DEBUG: cleaned='{cleaned}'")
    
    assert len(tools) == 1, f"Expected 1 tool, got {len(tools)}. Tools: {tools}"
    assert tools[0]["name"] == "bash", f"Expected name 'bash', got {tools[0]['name']}"
    assert tools[0]["input"] == {"command": "cat GEMINI.md"}, f"Expected input {{'command': 'cat GEMINI.md'}}, got {tools[0]['input']}"
    assert "```json" not in cleaned, f"Markdown block still present in cleaned text: {cleaned}"
    print("✓ Markdown JSON extraction passed")

def test_full_response_transform():
    print("Testing full response transformation...")
    raw_response = {
        "content": [
            {"type": "text", "text": "I will run this: <tool_code>{\"name\": \"bash\", \"arguments\": {\"cmd\": \"whoami\"}}</tool_code>"}
        ],
        "stop_reason": "end_turn"
    }
    
    transformed = transformer.transform_response(raw_response)
    
    assert transformed["stop_reason"] == "tool_use"
    assert len(transformed["content"]) == 2
    assert transformed["content"][1]["type"] == "tool_use"
    assert transformed["content"][1]["name"] == "bash"
    print("✓ Full response transformation passed")

if __name__ == "__main__":
    try:
        test_xml_extraction()
        test_markdown_json_extraction()
        test_full_response_transform()
        print("\nAll transformer tests passed!")
    except AssertionError as e:
        print(f"\nTest failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nAn error occurred: {e}")
        traceback.print_exc()
        sys.exit(1)
