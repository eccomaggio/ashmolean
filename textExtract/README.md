To do:
  check with Laura about the exact meaning of all of the fields - COMPLETED

create a test batch of 10 records -> Laura

===========================================================================
Feedback from Laura:

Hi Paul,

Thank you for this. It has been a fantastic test case and I think we are almost there! I have used what you sent to me and tested out an import definition that I had already created.

This has raised a couple of points to build into the work you are doing. I have adjusted the spreadsheet you sent to me, and have reattached it here. Perhaps we can go through this together when you are next in. The main changes that it would be great if you could incorporate would be:

The date should be formatted as “01/01/1992”.

Taking the first record as an example:

Unknown founder
After Alessandro ALGARDI (1598-1654)
1 and 2. Flagellators of Christ
23.2 cms (height 0of No. 1, figure with both arms raised);
23.5 cms (height of No. 2, figure with right arm only raised);
5.5 cms (height of plinths)
Bronze with a warm brown natural patina retaining extensive patches of translucent red varnish, now partially blackened, especially in the hollows. Hollow, lost-wax casts. There is a performation in the bronze below the loin cloth worn by No. 1, also a plug beside the ankle of the right foot of this figure. Both figures are very finely tooled withy the hair (including hair in armpits and pubes) sharply but boldly cut. The loin cloth is minutely punched. The figures must originally have held separately fashioned whips.
Both figures are mounted on plinths of oak veneered with brass, fire gilt, and with tortoiseshell, fitted with bronze scroll-feet and lion masks, also fire-gilt. (Some pieces of veneer are loose, broken, or missing especially below the lion heads on the back and side faces of both plinths.) There are circular paper labels belowe the plinths with black borders and 'J. FRANCIS & MARGARET MALLETT' printed, and with '27E' inscribed in ink, in the centre. Both plinths marked 'M 176/1' and 'M176/2' in black paint on the corner of the upper ormolu moulding of the plinth  where chamfered.
Bequeathed by J. F. Mallet, who died 7 January 1947. Received in the Museum during the last week of May 1947. No. 176 (1 and 2) in the inventory of his collection where [sic] valued at Â£100. No provenance supplied.
Described by Mallett as 'Finely modelled and highly finished. Beautiful brown patina. N. Italian. Attributed to Pietro Tacca.' The pair of figures are in fact cast from models almost certainly made by Algardi. Together with a figure of Christ bound to the column, they formed one of the most popular small sculptural groups (of silver as well as of bronze) in European art. The same model for Christ is employed with flagellators of a different design which may plausibly be attributed to Duquesnoy, who is indced credited by Bellori with the invention of a group of this subject (including Christ). Jennifer Montagu has proposed, convincingly, that the more vigorous of the pairs of flagellators-that represented by the Ashmolean's bronzes-originated in models by Algardi. She dates these models to the second half of the 1630s. Of this particular pair of bronzes she writes: 'The casts appear to be reasonably old, although the Boulle-type bases are no doubt of more recent date' (J. Montagu, Alessandro Algardi (London and New Haven, Conn.,1985), i.19z ii. 315-22, no. 9; and for the Ashmolean bronzes 319, no. 9 C. 19). The bases resemble in both style and materials French work of the late seventeenth century of the kind revived in the second half of the eighteenth century, but they may be of modern manufacture; certainly all screw fittings appear to be modern. The figures, which are of as high quality as any other bronze versions I have seen, seem to be either Italian or French. See also No. 434.

We should actually only be pulling in:

Bronze with a warm brown natural patina retaining extensive patches of translucent red varnish, now partially blackened, especially in the hollows. Hollow, lost-wax casts. There is a performation in the bronze below the loin cloth worn by No. 1, also a plug beside the ankle of the right foot of this figure. Both figures are very finely tooled withy the hair (including hair in armpits and pubes) sharply but boldly cut. The loin cloth is minutely punched. The figures must originally have held separately fashioned whips.
Both figures are mounted on plinths of oak veneered with brass, fire gilt, and with tortoiseshell, fitted with bronze scroll-feet and lion masks, also fire-gilt. (Some pieces of veneer are loose, broken, or missing especially below the lion heads on the back and side faces of both plinths.) There are circular paper labels belowe the plinths with black borders and 'J. FRANCIS & MARGARET MALLETT' printed, and with '27E' inscribed in ink, in the centre. Both plinths marked 'M 176/1' and 'M176/2' in black paint on the corner of the upper ormolu moulding of the plinth  where chamfered.
Bequeathed by J. F. Mallet, who died 7 January 1947. Received in the Museum during the last week of May 1947. No. 176 (1 and 2) in the inventory of his collection where [sic] valued at Â£100. No provenance supplied.
Described by Mallett as 'Finely modelled and highly finished. Beautiful brown patina. N. Italian. Attributed to Pietro Tacca.' The pair of figures are in fact cast from models almost certainly made by Algardi. Together with a figure of Christ bound to the column, they formed one of the most popular small sculptural groups (of silver as well as of bronze) in European art. The same model for Christ is employed with flagellators of a different design which may plausibly be attributed to Duquesnoy, who is indced credited by Bellori with the invention of a group of this subject (including Christ). Jennifer Montagu has proposed, convincingly, that the more vigorous of the pairs of flagellators-that represented by the Ashmolean's bronzes-originated in models by Algardi. She dates these models to the second half of the 1630s. Of this particular pair of bronzes she writes: 'The casts appear to be reasonably old, although the Boulle-type bases are no doubt of more recent date' (J. Montagu, Alessandro Algardi (London and New Haven, Conn.,1985), i.19z ii. 315-22, no. 9; and for the Ashmolean bronzes 319, no. 9 C. 19). The bases resemble in both style and materials French work of the late seventeenth century of the kind revived in the second half of the eighteenth century, but they may be of modern manufacture; certainly all screw fittings appear to be modern. The figures, which are of as high quality as any other bronze versions I have seen, seem to be either Italian or French. See also No. 434.

Ideally, but I don’t know if it possible, we would add line breaks to make it more readable, e.g.,

Bronze with a warm brown natural patina retaining extensive patches of translucent red varnish, now partially blackened, especially in the hollows. Hollow, lost-wax casts. There is a performation in the bronze below the loin cloth worn by No. 1, also a plug beside the ankle of the right foot of this figure. Both figures are very finely tooled withy the hair (including hair in armpits and pubes) sharply but boldly cut. The loin cloth is minutely punched. The figures must originally have held separately fashioned whips.

Both figures are mounted on plinths of oak veneered with brass, fire gilt, and with tortoiseshell, fitted with bronze scroll-feet and lion masks, also fire-gilt. (Some pieces of veneer are loose, broken, or missing especially below the lion heads on the back and side faces of both plinths.) There are circular paper labels belowe the plinths with black borders and 'J. FRANCIS & MARGARET MALLETT' printed, and with '27E' inscribed in ink, in the centre. Both plinths marked 'M 176/1' and 'M176/2' in black paint on the corner of the upper ormolu moulding of the plinth  where chamfered.

Bequeathed by J. F. Mallet, who died 7 January 1947. Received in the Museum during the last week of May 1947. No. 176 (1 and 2) in the inventory of his collection where [sic] valued at Â£100. No provenance supplied.

Described by Mallett as 'Finely modelled and highly finished. Beautiful brown patina. N. Italian. Attributed to Pietro Tacca.' The pair of figures are in fact cast from models almost certainly made by Algardi. Together with a figure of Christ bound to the column, they formed one of the most popular small sculptural groups (of silver as well as of bronze) in European art. The same model for Christ is employed with flagellators of a different design which may plausibly be attributed to Duquesnoy, who is indced credited by Bellori with the invention of a group of this subject (including Christ). Jennifer Montagu has proposed, convincingly, that the more vigorous of the pairs of flagellators-that represented by the Ashmolean's bronzes-originated in models by Algardi. She dates these models to the second half of the 1630s. Of this particular pair of bronzes she writes: 'The casts appear to be reasonably old, although the Boulle-type bases are no doubt of more recent date' (J. Montagu, Alessandro Algardi (London and New Haven, Conn.,1985), i.19z ii. 315-22, no. 9; and for the Ashmolean bronzes 319, no. 9 C. 19). The bases resemble in both style and materials French work of the late seventeenth century of the kind revived in the second half of the eighteenth century, but they may be of modern manufacture; certainly all screw fittings appear to be modern. The figures, which are of as high quality as any other bronze versions I have seen, seem to be either Italian or French. See also No. 434.

There are also a couple of typos which snuck in I think due to the scanning. I have highlighted these. I wonder what the best way to weed these out would be? Whether its an eyeballing process once the Further Text has been entered into MuseumPlus? Any thoughts you have would be greatly received.

Let me know what you think and we can build in time to meet and go over this when you are next in.

Many thanks,

Laura
