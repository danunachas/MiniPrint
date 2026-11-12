# Settings profiles

Ready-made MiniPrint settings for different printers, nozzles and filaments.

**Use one:** in MiniPrint press *Import…* (Settings profile section) and pick the `.miniprint-profile.json`
file. It is added to your profile list.

**Share yours:** tune MiniPrint until it prints well on your machine, press *Export…*, and add the file
here with a pull request (or attach it to an issue / discussion). Please name it
`<nozzle>_<filament>_<style>.miniprint-profile.json` and say in the `description` field which printer, nozzle,
layer height and filament it was tested with.

| file | tested with |
|---|---|
| `0.2mm-nozzle_PLA_tree.miniprint-profile.json` | the defaults: Bambu Lab printer, 0.2 mm nozzle, 0.06 mm layers, PLA |
| `0.2mm-nozzle_PLA_raft.miniprint-profile.json` | the same, with *No bed contact* (model floats on its supports) |

The files are plain JSON: `values` holds the settings by the id of the field in the app (`p_…` = support settings).
Settings a profile does not mention get their default value.
