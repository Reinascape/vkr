configure_file(
  input: 'main.py',
  output: 'kitsune',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

blueprint_compiler = find_program('blueprint-compiler')

ui_blueprints = [
  'window',
  'preferences_window',
  'search_dialog',
  'release_view',
  'filter_dialog',
  'player_view',
  'auth_dialog',
  'profile_view',
]

widget_blueprints = [
  'content_grid',
  'release_card',
  'genre_card',
  'franchise_card',
  'tag_card',
]

ui_blp_inputs = []
ui_blp_outputs = []
foreach name : ui_blueprints
  ui_blp_inputs += files('kitsune/ui' / name + '.blp')
  ui_blp_outputs += name + '.ui'
endforeach

widget_blp_inputs = []
widget_blp_outputs = []
foreach name : widget_blueprints
  widget_blp_inputs += files('kitsune/ui/widgets' / name + '.blp')
  widget_blp_outputs += name + '.ui'
endforeach

blueprints_ui = custom_target('blueprints-ui',
  input: ui_blp_inputs,
  output: ui_blp_outputs,
  command: [blueprint_compiler, 'batch-compile', '@OUTDIR@', '@CURRENT_SOURCE_DIR@/kitsune/ui', '@INPUT@'],
)

blueprints_widgets = custom_target('blueprints-widgets',
  input: widget_blp_inputs,
  output: widget_blp_outputs,
  command: [blueprint_compiler, 'batch-compile', '@OUTDIR@', '@CURRENT_SOURCE_DIR@/kitsune/ui/widgets', '@INPUT@'],
)

gnome.compile_resources(
  'net.armatik.Kitsune',
  'kitsune.gresource.xml',
  dependencies: [blueprints_ui, blueprints_widgets],
  gresource_bundle: true,
  install: true,
  install_dir: pkgdatadir,
)

blp_exclude = []
foreach name : ui_blueprints
  blp_exclude += 'ui' / name + '.blp'
endforeach
foreach name : widget_blueprints
  blp_exclude += 'ui/widgets' / name + '.blp'
endforeach

install_subdir('kitsune', install_dir: pkgdatadir,
  exclude_directories: ['__pycache__'],
  exclude_files: blp_exclude,
)
