import os
import sys

# Dette må stå før importen av src.resize_pro
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from make_print_sets import main

if __name__ == "__main__":
    main()
