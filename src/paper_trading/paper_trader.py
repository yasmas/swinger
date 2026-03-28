"""Compatibility shim — PaperTrader is now SwingBot.

This module re-exports SwingBot as PaperTrader so existing scripts
and imports continue to work. New code should use:

    from trading.swing_bot import SwingBot, load_config, main
"""

from trading.swing_bot import SwingBot as PaperTrader, load_config, main  # noqa: F401

if __name__ == "__main__":
    main()
