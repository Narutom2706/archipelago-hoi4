# Hearts of Iron IV × Archipelago

Joue l'Allemagne dans un multiworld [Archipelago](https://archipelago.gg) : chaque focus est verrouillé
par une clé, cachée dans ton jeu ou dans celui des autres. Finir un focus envoie un check.

## Pour jouer

Télécharge la dernière [release](../../releases) : `HOI4_Archipelago_vX.Y.Z.zip` et `hoi4.apworld`.

**Celui qui génère la partie** double-clique sur `hoi4.apworld` (le Launcher Archipelago l'installe),
puis utilise un YAML comme celui-ci :

```yaml
name: TonPseudo
game: Hearts of Iron IV
Hearts of Iron IV:
  victory_condition: defeat_allies   # ou defeat_ussr, world_conquest
```

**Le joueur HOI4** :

1. Dézippe l'archive où tu veux (garde l'exe et la DLL ensemble).
2. Lance `HOI4_Archipelago.exe`, entre l'adresse du serveur et ton nom de slot.
3. La première fois seulement : active le mod « Archipelago » dans le launcher Paradox (Playsets).
4. Lance HOI4 et démarre une partie avec l'Allemagne. Le reste est automatique (ne touche pas au
   clavier quelques secondes quand le programme passe sur le jeu pour armer la console).

Quand une clé arrive, une fenêtre « Clé Archipelago reçue » te dit quel focus est débloqué.

**Bon à savoir**
- Windows Defender peut se méfier de l'exe (il injecte une DLL dans le jeu, c'est son rôle).
- Après un patch de HOI4, l'exe régénère le mod tout seul. Si la DLL ne reconnaît pas la nouvelle
  version, le jeu tourne normalement mais les clés n'arrivent plus : attends une release à jour.
- Ironman non supporté (la console y est désactivée).

## Pour développer

| Fichier | Rôle |
|---|---|
| `hoi4_archipelago.py` | point d'entrée joueur (→ `HOI4_Archipelago.exe`) |
| `hoi4_archipelago_client.py` | client AP : game.log → checks, items → DLL |
| `patch_germany_tree.py` | génère le mod depuis le `germany.txt` installé (mode dev : met à jour le mapping) |
| `mapping_germany.json` | IDs des items/locations, **source de vérité** partagée exe ↔ APWorld |
| `hoi4_dll/` | DLL : exécute les commandes console reçues par le pipe `\\.\pipe\hoi4_ap` |
| `apworld/hoi4/` | l'APWorld |
| `tools/make_signature.py` | signature de la fonction console après un patch HOI4 |

```powershell
pip install -r requirements.txt
python -m pytest tests            # tests
python patch_germany_tree.py      # régénère mod + mapping (+ copie dans l'APWorld)
python mock_server.py             # faux serveur AP local
python hoi4_archipelago.py        # lance comme un joueur
```

**Publier** : `git tag v0.2.0 && git push origin v0.2.0`. La GitHub Action teste, compile la DLL (MSVC),
construit l'exe et le `.apworld`, et crée la release.

**Après un patch de HOI4** : regarder `logs/archipelago_dll.log`. Si la signature retrouve encore la
fonction console, rien à faire côté DLL. Sinon, retrouver la fonction, puis mettre à jour
`cfg::KNOWN_PE_TIMESTAMP`, `cfg::KNOWN_RVA` et `cfg::SIGNATURE` (`tools/make_signature.py <exe> <rva>`)
dans `dllmain.cpp`. Relancer `patch_germany_tree.py` : si des focus apparaissent ou
disparaissent, le mapping change et il faut publier une nouvelle release (exe + APWorld ensemble).
