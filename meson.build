project('kitsune',
  version: '0.8.5',
  meson_version: '>= 1.0.0',
  default_options: ['warning_level=2'],
)

i18n = import('i18n')
gnome = import('gnome')
python = import('python')

py_installation = python.find_installation('python3', modules: ['gi', 'cairo'])

APPLICATION_ID = 'net.armatik.Kitsune'

pkgdatadir = join_paths(get_option('prefix'), get_option('datadir'), 'kitsune')

conf = configuration_data()
conf.set('APPLICATION_ID', APPLICATION_ID)
conf.set('PYTHON', py_installation.full_path())
conf.set('VERSION', meson.project_version())
conf.set('PKGDATADIR', pkgdatadir)
conf.set('LOCALEDIR', join_paths(get_option('prefix'), get_option('localedir')))

subdir('src')
subdir('data')
subdir('po')
subdir('tests')

gnome.post_install(
  glib_compile_schemas: true,
  gtk_update_icon_cache: true,
  update_desktop_database: true,
)
