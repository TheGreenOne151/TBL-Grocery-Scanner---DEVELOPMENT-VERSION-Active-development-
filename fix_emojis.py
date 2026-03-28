# fix_emojis.py - Run this once to fix your backend file
import re

fix_map = {
    'ðŸ“Š': '📊',
    'ðŸŽ¯': '🎯',
    'ðŸ“ˆ': '📈',
    'ðŸ‘¥': '👥',
    'ðŸŒ±': '🌱',
    'ðŸ’°': '💰',
    'â­': '⭐',
    'ðŸ§ª': '🧪',
    'ðŸ”„': '🔍',
    'ðŸ ': '🏠',
    'â¤ï¸': '❤️',
    'â¬…ï¸': '⬅️',
    'ðŸ“¹': '📹',
    'ðŸ”§': '🔧',
    'ðŸ“±': '📱',
    'âœ…': '✅',
    'âœ—': '❌',
}

with open('elegant_app.py', 'r', encoding='utf-8') as f:
    content = f.read()

for old, new in fix_map.items():
    content = content.replace(old, new)

with open('elegant_app.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Emojis fixed!")
