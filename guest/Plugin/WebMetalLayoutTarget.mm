// The Metal.framework layouts the iOS bundle requires: iPadOS 17.7.11 (21H461), from the generated tables.

#import "WebMetalSurface.h"

#if !__has_include("../Generated/WebMetalTargetSurface.h")
#error "guest/Generated/WebMetalTargetSurface.h is missing: it is made from the user's own root (WebMetalSurface.h)"
#endif
#include "../Generated/WebMetalTargetSurface.h"

namespace webmetal {

const LayoutSet kExpectedLayouts = {"iPadOS 17.7.11 (21H461)", target::kBases,
                                    sizeof target::kBases / sizeof *target::kBases};

}  // namespace webmetal
