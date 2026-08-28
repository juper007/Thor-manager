import pprint

import modelopt
import modelopt.torch.quantization as mtq

print("modelopt", getattr(modelopt, "__version__", "?"))
pprint.pp(mtq.NVFP4_DEFAULT_CFG)
